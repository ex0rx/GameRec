import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from redis.exceptions import RedisError

from gamerec.api.auth import auth_error_handler
from gamerec.api.auth import router as auth_router
from gamerec.api.explanations import router as explanations_router
from gamerec.api.games import router as games_router
from gamerec.api.health import router as health_router
from gamerec.api.recommendations import router as recommendations_router
from gamerec.api.search import router as search_router
from gamerec.api.users import router as users_router
from gamerec.core.auth_privacy import (
    AuthPrivacyMiddleware,
    install_auth_log_filters,
    private_account_path,
)
from gamerec.core.auth_security import AuthError
from gamerec.core.config import settings
from gamerec.integrations.redis import create_redis_client
from gamerec.services.explanation_cache import ExplanationCache
from gamerec.services.manual_library_sync import LibrarySyncCoordinator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.library_sync_coordinator = LibrarySyncCoordinator(
        settings.manual_library_sync_cooldown_seconds
    )
    client = create_redis_client() if settings.explanation_cache_enabled else None
    app.state.explanation_cache = (
        ExplanationCache(
            client,
            settings.explanation_cache_ttl_seconds,
            settings.explanation_cache_version,
        )
        if client is not None
        else None
    )
    try:
        yield
    finally:
        app.state.library_sync_coordinator = None
        app.state.explanation_cache = None
        if client is not None:
            try:
                await client.aclose()
            except RedisError:
                logging.getLogger(__name__).warning("Explanation cache close failed")


async def unhandled_error_response(
    request: Request, _exc: Exception
) -> PlainTextResponse:
    # ServerErrorMiddleware creates unhandled 500s outside user middleware.
    headers = (
        {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if private_account_path(request.url.path)
        else None
    )
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


def create_app() -> FastAPI:
    install_auth_log_filters()
    application = FastAPI(lifespan=lifespan)
    application.add_exception_handler(AuthError, auth_error_handler)
    application.add_exception_handler(Exception, unhandled_error_response)
    if settings.auth_enabled:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.auth_frontend_origin],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type"],
        )
    # Wrap CORS as well so auth preflight responses retain privacy headers.
    application.add_middleware(AuthPrivacyMiddleware)
    for router in (
        health_router,
        games_router,
        search_router,
        explanations_router,
        users_router,
        recommendations_router,
        auth_router,
    ):
        application.include_router(router)
    return application


app = create_app()
