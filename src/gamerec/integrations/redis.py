"""One lazily connected Redis pool, owned by the API lifespan."""

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gamerec.core.config import settings


def create_redis_client() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )
