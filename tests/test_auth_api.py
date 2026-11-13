"""HTTP authentication contracts using synthetic tokens and mocked persistence."""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import auth
from gamerec.core.auth_security import AuthError
from gamerec.core.config import settings
from gamerec.db import get_db
from gamerec.integrations.steam_openid import OpenIDError, VerifiedIdentity
from gamerec.main import create_app

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"
PUBLIC = "p" * 43
PRIVATE = "b" * 43
SESSION = "s" * 43
ORIGIN = "http://localhost:5173"
RETURN_TO = f"{ORIGIN}/api/auth/steam/callback?login_id={PUBLIC}"
NS = "http://specs.openid.net/auth/2.0"


def callback():
    return {
        "login_id": PUBLIC,
        "openid.ns": NS,
        "openid.mode": "id_res",
        "openid.op_endpoint": "https://steamcommunity.com/openid/login",
        "openid.return_to": RETURN_TO,
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{USER}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{USER}",
        "openid.response_nonce": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        + "synthetic",
        "openid.assoc_handle": "synthetic-handle",
        "openid.signed": "op_endpoint,return_to,response_nonce,assoc_handle,claimed_id,identity",
        "openid.sig": base64.b64encode(b"synthetic-signature!!").decode(),
    }


@pytest.fixture
def enabled(monkeypatch):
    for name, value in {
        "environment": "development",
        "auth_enabled": True,
        "auth_public_base_url": ORIGIN + "/api",
        "auth_frontend_origin": ORIGIN,
        "auth_success_path": "/discover",
        "auth_cookie_secure": False,
        "auth_session_cookie_name": "gamerec_session",
        "auth_login_cookie_name": "gamerec_login",
    }.items():
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def services(monkeypatch, enabled):
    transaction = SimpleNamespace(
        login_id=PUBLIC,
        expected_return_to=RETURN_TO,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    identity = VerifiedIdentity(
        steamid64=USER,
        nonce=callback()["openid.response_nonce"],
        nonce_expires_at=datetime.now(UTC) + timedelta(minutes=12),
    )

    async def consume(_db, login_id, browser_secret):
        if login_id != PUBLIC or browser_secret != PRIVATE:
            raise AuthError()
        return transaction

    helpers = {
        "create_login_transaction": AsyncMock(return_value=(transaction, PRIVATE)),
        "consume_login_transaction": AsyncMock(side_effect=consume),
        "verify_assertion": AsyncMock(return_value=identity),
        "establish_session": AsyncMock(return_value=SESSION),
        "resolve_session_user": AsyncMock(
            side_effect=lambda _db, token: (
                SimpleNamespace(steamid64=USER) if token == SESSION else None
            )
        ),
        "logout_session": AsyncMock(),
    }
    for name, helper in helpers.items():
        monkeypatch.setattr(auth, name, helper)
    return SimpleNamespace(transaction=transaction, **helpers)


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, services):
    application = create_app()

    async def override_db():
        yield fake_db

    monkeypatch.setitem(application.dependency_overrides, get_db, override_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


def assert_private_headers(response):
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_login_uses_trusted_urls_and_temporary_protected_cookie(client, services):
    response = await client.get(
        "/auth/steam/login?next=https://evil.example",
        headers={"Host": "spoofed.example"},
    )
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "steamcommunity.com",
        "/openid/login",
    )
    query = parse_qs(url.query)
    assert query["openid.ns"] == [NS]
    assert query["openid.mode"] == ["checkid_setup"]
    assert query["openid.realm"] == [ORIGIN + "/"]
    assert query["openid.return_to"] == [RETURN_TO]
    assert (
        query["openid.claimed_id"]
        == query["openid.identity"]
        == [NS + "/identifier_select"]
    )
    assert "openid.state" not in query
    assert PRIVATE not in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Max-Age=600" in cookie
    assert "Domain=" not in cookie and "Secure" not in cookie
    services.create_login_transaction.assert_awaited_once()
    assert_private_headers(response)


async def test_callback_establishes_session_only_after_verification(client, services):
    response = await client.get(
        "/auth/steam/callback",
        params=callback(),
        headers={"Cookie": f"gamerec_login={PRIVATE}"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == ORIGIN + "/discover"
    assert SESSION not in response.text
    assert SESSION not in response.headers["location"]
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(
        value for value in cookies if value.startswith("gamerec_session=")
    )
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie and "Domain=" not in session_cookie
    assert any(
        f"gamerec_session={SESSION}" in cookie and "Max-Age=604800" in cookie
        for cookie in cookies
    )
    assert any(
        "gamerec_login=" in cookie and "Max-Age=0" in cookie for cookie in cookies
    )
    services.consume_login_transaction.assert_awaited_once()
    services.verify_assertion.assert_awaited_once()
    services.establish_session.assert_awaited_once()
    call = services.verify_assertion.await_args
    client = call.kwargs.get("client", call.args[0] if call.args else None)
    assert client.is_closed
    assert_private_headers(response)


@pytest.mark.parametrize(
    "params", [{}, {"login_id": PUBLIC}, [("login_id", PUBLIC), ("login_id", PUBLIC)]]
)
async def test_malformed_callback_is_safe_400_not_sensitive_422(
    client, services, params
):
    response = await client.get("/auth/steam/callback", params=params)
    assert response.status_code == 400
    assert "openid.sig" not in response.text and USER not in response.text
    services.establish_session.assert_not_called()
    assert_private_headers(response)


@pytest.mark.parametrize("status", [400, 502, 503, 504])
async def test_provider_failures_never_create_sessions(client, services, status):
    services.verify_assertion.side_effect = OpenIDError(status_code=status)
    response = await client.get(
        "/auth/steam/callback",
        params=callback(),
        headers={"Cookie": f"gamerec_login={PRIVATE}"},
    )
    assert response.status_code == status
    services.establish_session.assert_not_called()
    call = services.verify_assertion.await_args
    steam_client = call.kwargs.get("client", call.args[0] if call.args else None)
    assert steam_client.is_closed
    assert USER not in response.text and PRIVATE not in response.text
    assert_private_headers(response)


async def test_invalid_browser_transaction_never_contacts_steam(client, services):
    services.consume_login_transaction.side_effect = AuthError()
    response = await client.get("/auth/steam/callback", params=callback())
    assert response.status_code == 400
    services.verify_assertion.assert_not_called()
    services.establish_session.assert_not_called()


async def test_me_only_returns_verified_identity(client):
    response = await client.get(
        "/auth/me", headers={"Cookie": f"gamerec_session={SESSION}"}
    )
    assert response.status_code == 200
    assert response.json() == {"steamid64": USER}
    assert SESSION not in response.text
    assert_private_headers(response)


@pytest.mark.parametrize("cookie", [None, "bad", "x" * 43, SESSION + "x"])
async def test_missing_invalid_expired_or_revoked_session_returns_401(client, cookie):
    headers = {"Cookie": f"gamerec_session={cookie}"} if cookie else {}
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert USER not in response.text
    assert_private_headers(response)


async def test_duplicate_session_cookies_are_rejected(client):
    response = await client.get(
        "/auth/me",
        headers={"Cookie": f"gamerec_session={SESSION}; gamerec_session={'x' * 43}"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example"},
        {"Referer": "https://localhost:5173.evil.example/"},
        {"Origin": "https://evil.example", "Referer": ORIGIN + "/discover"},
    ],
)
async def test_logout_csrf_rejects_untrusted_or_missing_sources(
    client, services, headers
):
    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 403
    services.logout_session.assert_not_called()
    assert_private_headers(response)


@pytest.mark.parametrize(
    "headers", [{"Origin": ORIGIN}, {"Referer": ORIGIN + "/discover"}]
)
async def test_logout_idempotent_with_trusted_source_and_no_session(
    client, services, headers
):
    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 204 and response.content == b""
    services.logout_session.assert_awaited_once()
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "gamerec_session=" in value and "Max-Age=0" in value for value in cookies
    )
    assert_private_headers(response)


@pytest.mark.parametrize(
    "path,method",
    [
        ("/auth/steam/login", "get"),
        ("/auth/steam/callback", "get"),
        ("/auth/me", "get"),
        ("/auth/logout", "post"),
    ],
)
async def test_disabled_auth_returns_safe_503(
    client, services, monkeypatch, path, method
):
    monkeypatch.setattr(settings, "auth_enabled", False)
    response = await getattr(client, method)(path)
    assert response.status_code == 503
    for name in (
        "create_login_transaction",
        "consume_login_transaction",
        "resolve_session_user",
        "logout_session",
    ):
        getattr(services, name).assert_not_called()


@pytest.mark.parametrize(
    "helper,path",
    [
        ("create_login_transaction", "/auth/steam/login"),
        ("resolve_session_user", "/auth/me"),
    ],
)
async def test_database_failures_are_safe_503(client, services, helper, path):
    getattr(services, helper).side_effect = SQLAlchemyError(
        "synthetic secret SQL credentials"
    )
    response = await client.get(path, headers={"Cookie": f"gamerec_session={SESSION}"})
    assert response.status_code == 503
    assert "secret" not in response.text and USER not in response.text


async def test_cors_uses_only_explicit_frontend_origin(client):
    trusted = await client.options(
        "/auth/logout",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == ORIGIN
    assert trusted.headers["access-control-allow-credentials"] == "true"
    assert_private_headers(trusted)
    untrusted = await client.options(
        "/auth/logout",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert untrusted.status_code == 400
    assert "access-control-allow-origin" not in untrusted.headers
    assert_private_headers(untrusted)


async def test_production_cookies_have_browser_enforced_host_binding(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "auth_cookie_secure", True)
    response = await client.get("/auth/steam/login")
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-gamerec_login=")
    assert "Secure" in cookie and "HttpOnly" in cookie
    assert "Path=/" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie


async def test_overall_timeout_closes_client_without_creating_session(
    client, services, monkeypatch
):
    import asyncio

    monkeypatch.setattr(auth, "STEAM_VERIFICATION_TIMEOUT_SECONDS", 0.01)

    async def stalled(*args):
        await asyncio.Event().wait()

    services.verify_assertion.side_effect = stalled
    response = await client.get(
        "/auth/steam/callback",
        params=callback(),
        headers={"Cookie": f"gamerec_login={PRIVATE}"},
    )
    assert response.status_code == 504
    assert services.verify_assertion.await_args.args[0].is_closed
    services.establish_session.assert_not_called()


async def test_cancellation_closes_client_without_creating_session(client, services):
    import asyncio

    started = asyncio.Event()

    async def stalled(*args):
        started.set()
        await asyncio.Event().wait()

    services.verify_assertion.side_effect = stalled
    task = asyncio.create_task(
        client.get(
            "/auth/steam/callback",
            params=callback(),
            headers={"Cookie": f"gamerec_login={PRIVATE}"},
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert services.verify_assertion.await_args.args[0].is_closed
    services.establish_session.assert_not_called()


async def test_callback_slash_alias_is_rejected_without_forwarding_assertion(
    client, services
):
    response = await client.get(
        "/auth/steam/callback/",
        params=callback(),
        headers={"Cookie": f"gamerec_login={PRIVATE}", "Host": "untrusted.invalid"},
    )
    assert response.status_code == 400
    assert "location" not in response.headers
    assert_private_headers(response)
    services.consume_login_transaction.assert_not_called()
    services.verify_assertion.assert_not_called()
