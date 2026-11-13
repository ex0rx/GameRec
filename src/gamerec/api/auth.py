"""Steam authentication foundation; existing user endpoints remain unchanged."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.auth_security import (
    AuthError,
    clear_cookie,
    read_cookie,
    require_auth_enabled,
    require_trusted_origin,
    set_cookie,
)
from gamerec.core.config import settings
from gamerec.db import get_db
from gamerec.integrations.steam_openid import (
    OpenIDError,
    build_login_url,
    get_callback_parameters,
    verify_assertion,
)
from gamerec.models.user import User
from gamerec.schemas.auth import AuthenticatedUserResponse
from gamerec.services.auth_sessions import (
    consume_login_transaction,
    create_login_transaction,
    establish_session,
    logout_session,
    resolve_session_user,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)
STEAM_VERIFICATION_TIMEOUT_SECONDS = 20
STEAM_CONNECT_TIMEOUT_SECONDS = 5


def unavailable(exc: Exception) -> AuthError:
    logger.warning("Authentication storage unavailable (%s)", type(exc).__name__)
    return AuthError(503, "authentication_unavailable")


async def auth_error_handler(_request: Request, exc: AuthError) -> JSONResponse:
    messages = {
        "authentication_unavailable": "Authentication is temporarily unavailable.",
        "authentication_required": "Sign in with Steam to continue.",
        "csrf_failed": "The request origin is not trusted.",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": {"code": exc.code, "message": messages.get(exc.code, exc.message)}
        },
    )


async def get_current_user(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    require_auth_enabled()
    try:
        user = await resolve_session_user(
            db, read_cookie(request, settings.auth_session_cookie_name)
        )
    except (SQLAlchemyError, OSError) as exc:
        raise unavailable(exc) from None
    if user is None:
        raise AuthError(401, "authentication_required")
    return user


@router.get("/steam/login")
async def steam_login(db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    require_auth_enabled()
    try:
        transaction, browser_secret = await create_login_transaction(db)
    except (SQLAlchemyError, OSError) as exc:
        raise unavailable(exc) from None
    response = RedirectResponse(
        build_login_url(transaction.expected_return_to, settings.auth_realm),
        status_code=303,
    )
    set_cookie(
        response,
        settings.auth_login_cookie_name,
        browser_secret,
        settings.auth_login_ttl_seconds,
    )
    return response


@router.get("/steam/callback")
async def steam_callback(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    require_auth_enabled()
    transaction_claimed = False
    try:
        if len(request.scope.get("query_string", b"")) > 16384:
            raise AuthError()
        parameters = get_callback_parameters(list(request.query_params.multi_items()))
        transaction = await consume_login_transaction(
            db,
            parameters.get("login_id", ""),
            read_cookie(request, settings.auth_login_cookie_name),
        )
        transaction_claimed = True
        # Configuration changes invalidate outstanding redirects; Host/forwarded
        # headers cannot redefine this trusted public callback destination.
        if (
            transaction.expected_return_to
            != settings.auth_callback_url + "?login_id=" + transaction.login_id
        ):
            raise AuthError()
        async with asyncio.timeout(STEAM_VERIFICATION_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    STEAM_VERIFICATION_TIMEOUT_SECONDS,
                    connect=STEAM_CONNECT_TIMEOUT_SECONDS,
                ),
                follow_redirects=False,
            ) as client:
                identity = await verify_assertion(
                    client,
                    parameters,
                    transaction.expected_return_to,
                    datetime.now(UTC),
                )
        raw_session = await establish_session(
            db,
            transaction,
            identity,
            read_cookie(request, settings.auth_session_cookie_name),
        )
    except (AuthError, OpenIDError) as exc:
        error = (
            exc if isinstance(exc, AuthError) else AuthError(exc.status_code, exc.code)
        )
        response = await auth_error_handler(request, error)
        if transaction_claimed:
            clear_cookie(response, settings.auth_login_cookie_name)
        return response
    except (TimeoutError, httpx.TimeoutException):
        response = await auth_error_handler(
            request, AuthError(504, "steam_auth_timeout")
        )
        if transaction_claimed:
            clear_cookie(response, settings.auth_login_cookie_name)
        return response
    except (SQLAlchemyError, OSError) as exc:
        response = await auth_error_handler(request, unavailable(exc))
        if transaction_claimed:
            clear_cookie(response, settings.auth_login_cookie_name)
        return response
    response = RedirectResponse(settings.auth_success_url, status_code=303)
    set_cookie(
        response,
        settings.auth_session_cookie_name,
        raw_session,
        settings.auth_session_ttl_seconds,
    )
    clear_cookie(response, settings.auth_login_cookie_name)
    return response


@router.get("/me", response_model=AuthenticatedUserResponse)
async def auth_me(
    user: Annotated[User, Depends(get_current_user)],
) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(steamid64=user.steamid64)


@router.post("/logout", status_code=204)
async def auth_logout(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    require_trusted_origin(request)
    try:
        await logout_session(
            db, read_cookie(request, settings.auth_session_cookie_name)
        )
    except (SQLAlchemyError, OSError) as exc:
        raise unavailable(exc) from None
    response = Response(status_code=204)
    clear_cookie(response, settings.auth_session_cookie_name)
    clear_cookie(response, settings.auth_login_cookie_name)
    return response
