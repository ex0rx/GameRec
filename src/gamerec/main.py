import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.exceptions import RedisError

from gamerec.api.explanations import router as explanations_router
from gamerec.api.games import router as games_router
from gamerec.api.health import router as health_router
from gamerec.api.recommendations import router as recommendations_router
from gamerec.api.search import router as search_router
from gamerec.api.users import router as users_router
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


app = FastAPI(lifespan=lifespan)

app.include_router(health_router)
app.include_router(games_router)
app.include_router(search_router)
app.include_router(explanations_router)
app.include_router(users_router)
app.include_router(recommendations_router)
