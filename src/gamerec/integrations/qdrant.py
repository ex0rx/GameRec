from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings


def get_qdrant_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )
