from collections.abc import Sequence
from typing import TypedDict

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HasIdCondition,
    MatchValue,
    PayloadSchemaType,
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


async def ensure_game_payload_indexes(client: AsyncQdrantClient) -> None:
    """Ensure keyword indexes on an existing collection; caller owns the client.

    Call after collection setup, not during retrieval. Existing incompatible
    indexes are reported without replacing them or migrating the collection.
    """
    collection_name = settings.qdrant_game_collection
    info = await client.get_collection(collection_name)
    fields = ("genres", "categories")
    for field in fields:
        existing = info.payload_schema.get(field)
        if existing is not None and existing.data_type != PayloadSchemaType.KEYWORD:
            raise ValueError(f"Payload index {field!r} must have keyword type")
    for field in fields:
        if field not in info.payload_schema:
            await client.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
                wait=True,
            )


class SimilarGame(TypedDict):
    steam_app_id: int
    name: str
    score: float


async def find_similar_games(
    client: AsyncQdrantClient,
    steam_app_id: int,
    top_k: int = 5,
    *,
    genres: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[SimilarGame]:
    """Return nearest indexed games in descending cosine-score order.

    Missing targets and non-positive limits return an empty list. Point IDs are
    the authoritative Steam app IDs; absent names become empty strings. The
    caller owns the client. Collection/transport errors propagate to the caller.
    Candidates must match every supplied genre/category exactly. None and empty
    lists impose no restriction; filters never restrict the target lookup.
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

    query_filter = Filter(must_not=[HasIdCondition(has_id=[steam_app_id])])
    conditions = [
        FieldCondition(key=field, match=MatchValue(value=value))
        for field, values in (("genres", genres), ("categories", categories))
        for value in (values or [])
    ]
    if conditions:
        query_filter.must = conditions

    response = await client.query_points(
        collection_name=settings.qdrant_game_collection,
        query=vector,
        query_filter=query_filter,
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
