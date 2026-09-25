from collections.abc import Sequence
from typing import TypedDict

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Filter,
    HasIdCondition,
    PointStruct,
    VectorParams,
)

from gamerec.core.config import settings
from gamerec.integrations.qdrant import get_qdrant_client


async def ensure_game_collection(client: AsyncQdrantClient | None = None) -> None:
    """Create the configured collection if absent; preserve existing collections."""
    owns_client = client is None
    if client is None:
        client = get_qdrant_client()
    try:
        if await client.collection_exists(settings.qdrant_game_collection):
            return
        await client.create_collection(
            collection_name=settings.qdrant_game_collection,
            vectors_config=VectorParams(
                size=settings.embeddings_vector_size,
                distance=Distance.COSINE,
            ),
        )
    finally:
        if owns_client:
            await client.close()


async def upsert_game_points(
    client: AsyncQdrantClient,
    points: Sequence[PointStruct],
) -> int:
    """Write one bounded batch and wait until Qdrant has applied it."""
    if not points:
        return 0
    await client.upsert(
        collection_name=settings.qdrant_game_collection,
        points=points,
        wait=True,
    )
    return len(points)


class SimilarGame(TypedDict):
    steam_app_id: int
    name: str
    score: float


async def find_similar_games(
    client: AsyncQdrantClient,
    steam_app_id: int,
    top_k: int = 5,
) -> list[SimilarGame]:
    """Return nearest indexed games in descending cosine-score order.

    Missing targets and non-positive limits return an empty list. Point IDs are
    the authoritative Steam app IDs; absent names become empty strings. The
    caller owns the client. Collection/transport errors propagate to the caller.
    """
    if top_k <= 0:
        return []

    targets = await client.retrieve(
        collection_name=settings.qdrant_game_collection,
        ids=[steam_app_id],
        with_vectors=True,
        with_payload=False,
    )
    if not targets:
        return []
    vector = targets[0].vector
    if not isinstance(vector, list) or not vector or isinstance(vector[0], list):
        raise ValueError("Target point must contain an unnamed dense game vector")

    response = await client.query_points(
        collection_name=settings.qdrant_game_collection,
        query=vector,
        query_filter=Filter(must_not=[HasIdCondition(has_id=[steam_app_id])]),
        limit=top_k,
        with_payload=["name"],
        with_vectors=False,
    )
    results: list[SimilarGame] = []
    for point in response.points:
        name = (point.payload or {}).get("name")
        results.append(
            {
                "steam_app_id": int(point.id),
                "name": name if isinstance(name, str) else "",
                "score": point.score,
            }
        )
    return results
