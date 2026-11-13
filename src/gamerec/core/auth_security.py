"""Opaque cookies and trusted-origin validation for authentication routes."""

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request, Response

from gamerec.core.config import settings

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


class AuthError(Exception):
    def __init__(
        self, status_code: int = 400, code: str = "authentication_failed"
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = "Steam sign-in could not be completed. Start a new sign-in."
        super().__init__(self.message)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_token(value: str | None) -> bool:
    return isinstance(value, str) and TOKEN_PATTERN.fullmatch(value) is not None


def cookie_name(base_name: str) -> str:
    return f"__Host-{base_name}" if settings.auth_cookie_secure else base_name


def read_cookie(request: Request, base_name: str) -> str | None:
    name = cookie_name(base_name)
    # Reject ambiguous duplicate cookies rather than choosing an attacker-controlled path.
    entries = [
        part.strip().partition("=")
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
    ]
    matches = [value for key, separator, value in entries if separator and key == name]
    return matches[0] if len(matches) == 1 and valid_token(matches[0]) else None


def set_cookie(response: Response, base_name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        cookie_name(base_name),
        value,
        max_age=max_age,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response, base_name: str) -> None:
    response.delete_cookie(
        cookie_name(base_name),
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def require_auth_enabled() -> None:
    if not settings.auth_enabled:
        raise AuthError(503, "authentication_unavailable")


def require_trusted_origin(request: Request) -> None:
    require_auth_enabled()
    parsed = urlsplit(settings.auth_public_base_url or "")
    allowed = {
        settings.auth_frontend_origin,
        urlunsplit((parsed.scheme, parsed.netloc, "", "", "")),
    }
    origins = request.headers.getlist("origin")
    if origins:
        if len(origins) != 1 or origins[0] not in allowed:
            raise AuthError(403, "csrf_failed")
        return
    referers = request.headers.getlist("referer")
    if len(referers) != 1:
        raise AuthError(403, "csrf_failed")
    try:
        parsed = urlsplit(referers[0])
        if (
            parsed.username is not None
            or parsed.password is not None
            or "\\" in referers[0]
            or urlunsplit((parsed.scheme, parsed.netloc, "", "", "")) not in allowed
        ):
            raise ValueError("Untrusted source")
    except ValueError:
        raise AuthError(403, "csrf_failed") from None
