"""Steam-only OpenID 2.0 verification; browser assertions are untrusted input."""

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree

import httpx

OPENID_NAMESPACE = "http://specs.openid.net/auth/2.0"
IDENTIFIER_SELECT = f"{OPENID_NAMESPACE}/identifier_select"
STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
SIGNON_TYPE = f"{OPENID_NAMESPACE}/signon"
SERVER_TYPE = f"{OPENID_NAMESPACE}/server"
XRD_NAMESPACE = "xri://$xrd*($v*2.0)"
XRDS_NAMESPACE = "xri://$xrds"
NONCE_MAX_AGE_SECONDS = 600
NONCE_CLOCK_SKEW_SECONDS = 120
MAX_CALLBACK_BYTES = 16_384
MAX_VERIFICATION_BYTES = 16_384
MAX_DISCOVERY_BYTES = 65_536
REQUIRED_SIGNED_FIELDS = frozenset(
    {
        "op_endpoint",
        "return_to",
        "response_nonce",
        "assoc_handle",
        "claimed_id",
        "identity",
    }
)
IDENTITY_PATTERN = re.compile(
    r"https?://steamcommunity\.com/openid/id/([1-9][0-9]{16})"
)
FIELD_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
NONCE_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z[\x21-\x7e]*"
)


class OpenIDError(Exception):
    """Public error fields contain no assertions, identities or exception URLs."""

    def __init__(
        self,
        status_code: int = 400,
        code: str = "authentication_failed",
        message: str = "Steam sign-in could not be verified. Start a new sign-in.",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VerifiedIdentity:
    steamid64: str
    nonce: str
    nonce_expires_at: datetime


def get_callback_parameters(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Preserve decoded field values while rejecting ambiguous query parameters."""
    if not pairs or len(pairs) > 64:
        raise OpenIDError()
    parameters: dict[str, str] = {}
    size = 0
    for key, value in pairs:
        size += len(key.encode()) + len(value.encode())
        if (
            key in parameters
            or len(key) > 128
            or size > MAX_CALLBACK_BYTES
            or (key != "login_id" and not key.startswith("openid."))
            or (key.startswith("openid.") and not FIELD_PATTERN.fullmatch(key[7:]))
            or any(ord(char) < 32 or ord(char) == 127 for char in key + value)
        ):
            raise OpenIDError()
        parameters[key] = value
    if not parameters.get("login_id"):
        raise OpenIDError()
    return parameters


def build_login_url(return_to: str, realm: str) -> str:
    return (
        STEAM_OPENID_ENDPOINT
        + "?"
        + urlencode(
            {
                "openid.ns": OPENID_NAMESPACE,
                "openid.mode": "checkid_setup",
                "openid.return_to": return_to,
                "openid.realm": realm,
                "openid.identity": IDENTIFIER_SELECT,
                "openid.claimed_id": IDENTIFIER_SELECT,
            }
        )
    )


def _validate_assertion(
    parameters: dict[str, str], expected_return_to: str, now: datetime
) -> tuple[str, str, datetime]:
    # Callers may supply a dictionary directly; validate its contents as well.
    get_callback_parameters(list(parameters.items()))
    if (
        parameters.get("openid.ns") != OPENID_NAMESPACE
        or parameters.get("openid.mode") != "id_res"
        or parameters.get("openid.op_endpoint") != STEAM_OPENID_ENDPOINT
        or parameters.get("openid.return_to") != expected_return_to
    ):
        raise OpenIDError()
    if parse_qs(urlsplit(expected_return_to).query).get("login_id") != [
        parameters["login_id"]
    ]:
        raise OpenIDError()
    claimed_id = parameters.get("openid.claimed_id", "")
    match = IDENTITY_PATTERN.fullmatch(claimed_id)
    if match is None or parameters.get("openid.identity") != claimed_id:
        raise OpenIDError()
    identity_digits = match.group(1)
    if (
        not 0 < int(identity_digits) < 2**64
        or str(int(identity_digits)) != identity_digits
    ):
        raise OpenIDError()
    signed = parameters.get("openid.signed", "").split(",")
    # The signed-field list can itself be signed; forward that value unchanged.
    # A signature field cannot include its own signature in the signed payload.
    if (
        len(signed) != len(set(signed))
        or not REQUIRED_SIGNED_FIELDS.issubset(signed)
        or any(
            not FIELD_PATTERN.fullmatch(field)
            or field == "sig"
            or not parameters.get(f"openid.{field}")
            for field in signed
        )
    ):
        raise OpenIDError()
    signature = parameters.get("openid.sig", "")
    try:
        decoded_signature = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error):
        raise OpenIDError() from None
    if not decoded_signature or len(signature) > 2048:
        raise OpenIDError()
    nonce = parameters.get("openid.response_nonce", "")
    if len(nonce) > 255 or NONCE_PATTERN.fullmatch(nonce) is None:
        raise OpenIDError()
    try:
        issued_at = datetime.strptime(nonce[:20], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise OpenIDError() from None
    age = (now - issued_at).total_seconds()
    if age > NONCE_MAX_AGE_SECONDS or age < -NONCE_CLOCK_SKEW_SECONDS:
        raise OpenIDError()
    retain_until = issued_at + timedelta(
        seconds=NONCE_MAX_AGE_SECONDS + NONCE_CLOCK_SKEW_SECONDS
    )
    return claimed_id, nonce, retain_until


async def _bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limit: int,
    data: dict[str, str] | None = None,
) -> bytes:
    try:
        async with client.stream(
            method,
            url,
            data=data,
            headers={
                "Accept-Encoding": "identity",
                "Accept": "application/xrds+xml" if method == "GET" else "text/plain",
            },
            follow_redirects=False,
        ) as response:
            if response.status_code >= 400:
                raise OpenIDError(
                    503,
                    "steam_auth_unavailable",
                    "Steam sign-in is unavailable. Try again later.",
                )
            if response.status_code != 200:
                raise OpenIDError(
                    502,
                    "steam_auth_invalid_response",
                    "Steam returned an unusable sign-in response.",
                )
            if (
                response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise OpenIDError(
                    502,
                    "steam_auth_invalid_response",
                    "Steam returned an unusable sign-in response.",
                )
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunks) + len(chunk) > limit:
                    raise OpenIDError(
                        502,
                        "steam_auth_invalid_response",
                        "Steam returned an unusable sign-in response.",
                    )
                chunks.extend(chunk)
            return bytes(chunks)
    except httpx.TimeoutException:
        raise OpenIDError(
            504, "steam_auth_timeout", "Steam sign-in timed out. Start a new sign-in."
        ) from None
    except httpx.HTTPError:
        raise OpenIDError(
            503,
            "steam_auth_unavailable",
            "Steam sign-in is unavailable. Try again later.",
        ) from None


def _validate_verification_response(body: bytes) -> None:
    try:
        text = body.decode("utf-8")
        values: dict[str, str] = {}
        lines = text.split("\n")
        if lines and not lines[-1]:
            lines.pop()
        for line in lines:
            if not line or ":" not in line:
                raise ValueError
            key, value = line.split(":", 1)
            if key in values or not FIELD_PATTERN.fullmatch(key):
                raise ValueError
            values[key] = value
        if values.get("ns") != OPENID_NAMESPACE or values.get("is_valid") not in {
            "true",
            "false",
        }:
            raise ValueError
    except (UnicodeError, ValueError):
        raise OpenIDError(
            502,
            "steam_auth_invalid_response",
            "Steam returned an unusable sign-in response.",
        ) from None
    if values["is_valid"] != "true":
        raise OpenIDError()


def _validate_discovery(body: bytes, claimed_id: str) -> None:
    try:
        text = body.decode("utf-8-sig")
        if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError
        root = ElementTree.fromstring(text)  # Bounded UTF-8 XML without DTD/entities.
        if root.tag != f"{{{XRDS_NAMESPACE}}}XRDS":
            raise ValueError
        for service in root.findall(
            f"{{{XRD_NAMESPACE}}}XRD/{{{XRD_NAMESPACE}}}Service"
        ):
            types = {
                (node.text or "").strip()
                for node in service.findall(f"{{{XRD_NAMESPACE}}}Type")
            }
            providers = {
                (node.text or "").strip()
                for node in service.findall(f"{{{XRD_NAMESPACE}}}URI")
            }
            local_ids = [
                (node.text or "").strip()
                for node in service.findall(f"{{{XRD_NAMESPACE}}}LocalID")
            ]
            if (
                SIGNON_TYPE in types
                and SERVER_TYPE not in types
                and STEAM_OPENID_ENDPOINT in providers
                and (not local_ids or local_ids == [claimed_id])
            ):
                return
        raise ValueError
    except (UnicodeError, ValueError, ElementTree.ParseError):
        raise OpenIDError(
            502,
            "steam_auth_invalid_response",
            "Steam returned an unusable sign-in response.",
        ) from None


async def verify_assertion(
    client: httpx.AsyncClient,
    parameters: dict[str, str],
    expected_return_to: str,
    now: datetime,
) -> VerifiedIdentity:
    claimed_id, nonce, retain_until = _validate_assertion(
        parameters, expected_return_to, now
    )
    payload = {
        key: value for key, value in parameters.items() if key.startswith("openid.")
    }
    payload["openid.mode"] = "check_authentication"
    verification = await _bounded_request(
        client,
        "POST",
        STEAM_OPENID_ENDPOINT,
        limit=MAX_VERIFICATION_BYTES,
        data=payload,
    )
    _validate_verification_response(verification)
    # Steam documents HTTP claimed-ID labels; they are identifiers, not transport.
    discovery_url = "https://" + claimed_id.split("://", 1)[1]
    discovery = await _bounded_request(
        client, "GET", discovery_url, limit=MAX_DISCOVERY_BYTES
    )
    _validate_discovery(discovery, claimed_id)
    return VerifiedIdentity(claimed_id.rsplit("/", 1)[1], nonce, retain_until)
