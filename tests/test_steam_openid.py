"""Steam OpenID protocol tests use synthetic assertions and MockTransport only."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from gamerec.integrations import steam_openid as openid

NOW = datetime(2026, 11, 13, 4, 0, tzinfo=UTC)
USER = "76561190000000001"  # Synthetic fixture, never a configured account.
LOGIN_ID = "public-transaction"
RETURN_TO = "http://localhost:5173/api/auth/steam/callback?login_id=" + LOGIN_ID
IDENTITY = "https://steamcommunity.com/openid/id/" + USER
NONCE = "2026-11-13T04:00:00Zsynthetic-nonce"
VALID_DIRECT = f"ns:{openid.OPENID_NAMESPACE}\nis_valid:true\n".encode()


def assertion(**changes):
    result = {
        "login_id": LOGIN_ID,
        "openid.ns": openid.OPENID_NAMESPACE,
        "openid.mode": "id_res",
        "openid.op_endpoint": openid.STEAM_OPENID_ENDPOINT,
        "openid.return_to": RETURN_TO,
        "openid.claimed_id": IDENTITY,
        "openid.identity": IDENTITY,
        "openid.response_nonce": NONCE,
        "openid.assoc_handle": "synthetic-handle",
        "openid.signed": "op_endpoint,identity,claimed_id,return_to,assoc_handle,response_nonce",
        "openid.sig": base64.b64encode(b"synthetic-signature").decode(),
    }
    result.update(changes)
    return result


def xrds(
    *, provider=openid.STEAM_OPENID_ENDPOINT, kind=openid.SIGNON_TYPE, local_id=None
):
    local = "" if local_id is None else f"<LocalID>{local_id}</LocalID>"
    return (
        f'<xrds:XRDS xmlns:xrds="{openid.XRDS_NAMESPACE}" '
        f'xmlns="{openid.XRD_NAMESPACE}"><XRD><Service>'
        f"<Type>{kind}</Type><URI>{provider}</URI>{local}"
        "</Service></XRD></xrds:XRDS>"
    ).encode()


async def verify(parameters=None, *, handler=None, now=NOW):
    requests = []

    def respond(request):
        requests.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(
            200, content=VALID_DIRECT if request.method == "POST" else xrds()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await openid.verify_assertion(
            client, parameters or assertion(), RETURN_TO, now
        )
    return result, requests


def test_login_url_uses_supported_openid_parameters_only():
    url = openid.build_login_url(RETURN_TO, "http://localhost:5173/")
    assert urlsplit(url)._replace(query="").geturl() == openid.STEAM_OPENID_ENDPOINT
    fields = parse_qs(urlsplit(url).query)
    assert fields == {
        "openid.ns": [openid.OPENID_NAMESPACE],
        "openid.mode": ["checkid_setup"],
        "openid.return_to": [RETURN_TO],
        "openid.realm": ["http://localhost:5173/"],
        "openid.identity": [openid.IDENTIFIER_SELECT],
        "openid.claimed_id": [openid.IDENTIFIER_SELECT],
    }


@pytest.mark.parametrize(
    "pairs",
    [
        [],
        [("login_id", "a"), ("login_id", "b")],
        [("login_id", "a"), ("openid.identity", "a"), ("openid.identity", "a")],
        [("login_id", "a"), ("next", "https://attacker.invalid")],
        [("login_id", "a"), ("openid.", "value")],
        [("login_id", "a"), ("openid.mode", "id_res\n")],
        [("openid.mode", "id_res")],
        [("login_id", "")],
        [("login_id", "a"), ("openid.ns", "x" * openid.MAX_CALLBACK_BYTES)],
        [("login_id", "a"), *[(f"openid.x{i}", "x") for i in range(65)]],
    ],
)
def test_callback_parser_rejects_ambiguous_or_unexpected_input(pairs):
    with pytest.raises(openid.OpenIDError) as error:
        openid.get_callback_parameters(pairs)
    assert error.value.status_code == 400


def test_callback_parser_preserves_exact_values():
    parameters = assertion(**{"openid.assoc_handle": "{HMAC-SHA256}{handle+value}"})
    assert openid.get_callback_parameters(list(parameters.items())) == parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["https", "http"])
async def test_valid_assertion_is_verified_and_discovered_over_https(scheme):
    identity = f"{scheme}://steamcommunity.com/openid/id/{USER}"
    parameters = assertion(
        **{"openid.claimed_id": identity, "openid.identity": identity}
    )
    verified, requests = await verify(parameters)
    assert verified == openid.VerifiedIdentity(
        USER, NONCE, NOW + timedelta(seconds=720)
    )
    assert [request.method for request in requests] == ["POST", "GET"]
    assert all(request.url.scheme == "https" for request in requests)
    assert str(requests[0].url) == openid.STEAM_OPENID_ENDPOINT
    posted = parse_qs(requests[0].content.decode())
    expected = {
        key: [value] for key, value in parameters.items() if key.startswith("openid.")
    }
    expected["openid.mode"] = ["check_authentication"]
    assert posted == expected
    assert "login_id" not in posted
    assert str(requests[1].url) == IDENTITY
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"openid.ns": "http://specs.openid.net/auth/1.1"},
        {"openid.mode": "cancel"},
        {"openid.mode": "check_authentication"},
        {"openid.op_endpoint": "https://attacker.invalid/openid/login"},
        {"openid.op_endpoint": "http://steamcommunity.com/openid/login"},
        {"openid.return_to": RETURN_TO + "&next=https://attacker.invalid"},
        {"login_id": "another-transaction"},
        {"openid.identity": IDENTITY + "0"},
        {"openid.claimed_id": IDENTITY + "/"},
        {
            "openid.claimed_id": "https://steamcommunity.com.attacker.invalid/openid/id/"
            + USER
        },
        {"openid.claimed_id": "https://user@steamcommunity.com/openid/id/" + USER},
        {"openid.claimed_id": IDENTITY + "?query=1"},
        {"openid.claimed_id": IDENTITY + "#fragment"},
        {"openid.claimed_id": "https://steamcommunity.com:443/openid/id/" + USER},
        {"openid.claimed_id": "https://steamcommunity.com/openid/id/" + "７" * 17},
        {"openid.sig": "not base64"},
        {"openid.sig": ""},
        {
            "openid.signed": "op_endpoint,return_to,response_nonce,assoc_handle,claimed_id"
        },
        {
            "openid.signed": "op_endpoint,identity,claimed_id,return_to,assoc_handle,response_nonce,identity"
        },
        {
            "openid.signed": "op_endpoint,identity,claimed_id,return_to,assoc_handle,response_nonce,unknown"
        },
        {
            "openid.signed": "op_endpoint,identity,claimed_id,return_to,assoc_handle,response_nonce,sig"
        },
        {
            "openid.signed": "op_endpoint, identity,claimed_id,return_to,assoc_handle,response_nonce"
        },
        {"openid.assoc_handle": ""},
    ],
)
async def test_invalid_assertions_are_rejected_before_network(changes):
    requests = []

    def forbidden(request):
        requests.append(request)
        pytest.fail("Malformed input must not contact Steam")

    with pytest.raises(openid.OpenIDError) as error:
        await verify(assertion(**changes), handler=forbidden)
    assert error.value.status_code == 400
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "openid.ns",
        "openid.identity",
        "openid.signed",
        "openid.sig",
        "openid.response_nonce",
    ],
)
async def test_missing_required_fields_are_rejected(field):
    parameters = assertion()
    del parameters[field]
    with pytest.raises(openid.OpenIDError):
        await verify(
            parameters, handler=lambda _: pytest.fail("Must not contact Steam")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nonce",
    [
        "2026-11-13T03:49:59Ztoo-old",
        "2026-11-13T04:02:01Ztoo-new",
        "2026-11-13T04:00:00.1Zfractional",
        "2026-11-13T04:00:00+00:00offset",
        "2026-02-30T04:00:00Zinvalid-date",
        "2026-11-13T04:00:00Z space",
        "2026-11-13T04:00:00Z" + "a" * 236,
    ],
)
async def test_invalid_nonce_timestamps_are_rejected(nonce):
    with pytest.raises(openid.OpenIDError):
        await verify(
            assertion(**{"openid.response_nonce": nonce}),
            handler=lambda _: pytest.fail("Must not contact Steam"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [-600, 120])
async def test_nonce_age_boundaries_are_accepted(seconds):
    issued = NOW + timedelta(seconds=seconds)
    nonce = issued.strftime("%Y-%m-%dT%H:%M:%SZ") + "unique"
    result, _ = await verify(assertion(**{"openid.response_nonce": nonce}))
    assert result.nonce_expires_at == issued + timedelta(seconds=720)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,status",
    [
        (f"ns:{openid.OPENID_NAMESPACE}\nis_valid:false\n".encode(), 400),
        (b"is_valid:true\n", 502),
        (VALID_DIRECT + b"is_valid:false\n", 502),
        (f"ns:{openid.OPENID_NAMESPACE}\nis_valid: true\n".encode(), 502),
        (f"ns:{openid.OPENID_NAMESPACE}\nis_valid:TRUE\n".encode(), 502),
        (b"\xff", 502),
        (VALID_DIRECT + b"\n", 502),
        (f"ns:{openid.OPENID_NAMESPACE}\u0085is_valid:true\n".encode(), 502),
    ],
)
async def test_direct_response_is_parsed_strictly(body, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=body)

    with pytest.raises(openid.OpenIDError) as error:
        await verify(handler=handler)
    assert error.value.status_code == status
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "discovery",
    [
        b"<broken>",
        b"<html>not discovery</html>",
        xrds(provider="https://attacker.invalid/openid/login"),
        xrds(kind=openid.SERVER_TYPE),
        xrds(local_id=IDENTITY + "0"),
        b'<!DOCTYPE x [<!ENTITY data "value">]>' + xrds(),
        xrds().decode().encode("utf-16"),
        xrds().replace(b"<Service>", b"<Service><LocalID>wrong</LocalID>"),
    ],
)
async def test_invalid_discovery_is_rejected(discovery):
    with pytest.raises(openid.OpenIDError) as error:
        await verify(
            handler=lambda request: httpx.Response(
                200, content=VALID_DIRECT if request.method == "POST" else discovery
            )
        )
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_discovery_local_identifier_can_match_the_claim():
    result, _ = await verify(
        handler=lambda request: httpx.Response(
            200,
            content=VALID_DIRECT
            if request.method == "POST"
            else xrds(local_id=IDENTITY),
        )
    )
    assert result.steamid64 == USER


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["verification", "discovery"])
@pytest.mark.parametrize(
    "status,expected", [(302, 502), (403, 503), (429, 503), (500, 503)]
)
async def test_upstream_statuses_are_safe_and_redirects_are_not_followed(
    phase, status, expected
):
    requests = []

    def handler(request):
        requests.append(request)
        if phase == "discovery" and request.method == "POST":
            return httpx.Response(200, content=VALID_DIRECT)
        return httpx.Response(
            status, headers={"Location": "https://attacker.invalid/sensitive"}
        )

    with pytest.raises(openid.OpenIDError) as error:
        await verify(handler=handler)
    assert error.value.status_code == expected
    assert "sensitive" not in str(error.value)
    assert len(requests) == (1 if phase == "verification" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["verification", "discovery"])
@pytest.mark.parametrize(
    "exception,expected", [(httpx.ConnectError, 503), (httpx.ReadTimeout, 504)]
)
async def test_network_failures_do_not_leak_exception_urls_or_retry(
    phase, exception, expected
):
    requests = []

    def handler(request):
        requests.append(request)
        if phase == "discovery" and request.method == "POST":
            return httpx.Response(200, content=VALID_DIRECT)
        raise exception("secret assertion URL")

    with pytest.raises(openid.OpenIDError) as error:
        await verify(handler=handler)
    assert error.value.status_code == expected
    assert "secret" not in str(error.value)
    assert len(requests) == (1 if phase == "verification" else 2)


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["verification", "discovery"])
async def test_stream_response_size_is_bounded_and_closed(phase):
    limit = (
        openid.MAX_VERIFICATION_BYTES
        if phase == "verification"
        else openid.MAX_DISCOVERY_BYTES
    )
    stream = TrackedStream([b"x" * (limit // 2), b"x" * (limit // 2 + 1)])

    def handler(request):
        if phase == "discovery" and request.method == "POST":
            return httpx.Response(200, content=VALID_DIRECT)
        return httpx.Response(200, stream=stream)

    with pytest.raises(openid.OpenIDError) as error:
        await verify(handler=handler)
    assert error.value.status_code == 502
    assert stream.closed


@pytest.mark.asyncio
async def test_compressed_responses_are_not_expanded_unboundedly():
    with pytest.raises(openid.OpenIDError) as error:
        await verify(
            handler=lambda _: httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=TrackedStream([b"not-gzip"]),
            )
        )
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_cancellation_closes_the_pending_response():
    started = asyncio.Event()

    class WaitingStream(TrackedStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    stream = WaitingStream([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        task = asyncio.create_task(
            openid.verify_assertion(client, assertion(), RETURN_TO, NOW)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


@pytest.mark.asyncio
async def test_signed_field_list_may_itself_be_signed_and_is_forwarded_unchanged():
    parameters = assertion()
    parameters["openid.signed"] = "signed," + parameters["openid.signed"]
    result, requests = await verify(parameters)
    assert result.steamid64 == USER
    assert parse_qs(requests[0].content.decode())["openid.signed"] == [
        parameters["openid.signed"]
    ]
