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
    PointIdsList,
    PointStruct,
    Record,
    VectorParams,
)

from gamerec.core.config import settings
from gamerec.integrations.qdrant import get_qdrant_client


def embedding_collection_metadata() -> dict[str, object]:
    """Durable identity for collections created by GameRec."""
    return {
        "gamerec_embeddings": {
            "model_name": settings.embeddings_model_name,
            "model_revision": settings.embeddings_model_revision,
            "vector_size": settings.embeddings_vector_size,
        }
    }


async def validate_game_collection(
    client: AsyncQdrantClient, *, collection_name: str | None = None
) -> None:
    name = collection_name or settings.qdrant_game_collection
    info = await client.get_collection(name)
    vectors = info.config.params.vectors
    if (
        not isinstance(vectors, VectorParams)
        or vectors.size != settings.embeddings_vector_size
        or vectors.distance != Distance.COSINE
    ):
        raise ValueError(
            f"Collection {name!r} has incompatible vector size/type/distance"
        )
    expected = embedding_collection_metadata()["gamerec_embeddings"]
    actual = (info.config.metadata or {}).get("gamerec_embeddings")
    if actual != expected:
        raise ValueError(
            f"Collection {name!r} has missing or incompatible model/revision binding; "
            "rebuild into a new collection name"
        )


async def ensure_game_collection(
    client: AsyncQdrantClient | None = None, *, collection_name: str | None = None
) -> None:
    """Create a bound collection or validate it; never adopt/relabel old data."""
    name = collection_name or settings.qdrant_game_collection
    owns_client = client is None
    if client is None:
        client = get_qdrant_client()
    try:
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=settings.embeddings_vector_size,
                    distance=Distance.COSINE,
                ),
                metadata=embedding_collection_metadata(),
            )
        # Also detects servers that did not persist collection metadata.
        await validate_game_collection(client, collection_name=name)
    finally:
        if owns_client:
            await client.close()


async def upsert_game_points(
    client: AsyncQdrantClient,
    points: Sequence[PointStruct],
    *,
    collection_name: str | None = None,
) -> int:
    """Write one bounded batch and wait until Qdrant has applied it."""
    if not points:
        return 0
    name = collection_name or settings.qdrant_game_collection
    await validate_game_collection(client, collection_name=name)
    await client.upsert(
        collection_name=name,
        points=points,
        wait=True,
    )
    return len(points)


async def ensure_game_payload_indexes(
    client: AsyncQdrantClient, *, collection_name: str | None = None
) -> None:
    """Ensure keyword indexes on an existing collection; caller owns the client.

    Call after collection setup, not during retrieval. Existing incompatible
    indexes are reported without replacing them or migrating the collection.
    """
    collection_name = collection_name or settings.qdrant_game_collection
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

    await validate_game_collection(client)
    targets = await client.retrieve(
        collection_name=settings.qdrant_game_collection,
        ids=[steam_app_id],
        with_vectors=True,
        with_payload=True,
    )
    if not targets:
        return []
    vector = targets[0].vector
    target_name = (targets[0].payload or {}).get("name", "")
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
    return results, target_name


async def get_game_point_payloads(
    client: AsyncQdrantClient, ids: list[int], *, collection_name: str
) -> dict[int, dict]:
    points = await client.retrieve(
        collection_name=collection_name, ids=ids, with_payload=True, with_vectors=False
    )
    return {int(point.id): point.payload or {} for point in points}


async def scroll_game_points(
    client: AsyncQdrantClient,
    *,
    collection_name: str,
    batch_size: int,
    offset: int | str | None = None,
) -> tuple[list[Record], int | str | None]:
    return await client.scroll(
        collection_name=collection_name,
        limit=batch_size,
        offset=offset,
        with_payload=False,
        with_vectors=False,
    )


async def delete_game_points(
    client: AsyncQdrantClient, ids: list[int], *, collection_name: str
) -> int:
    """Delete only explicit IDs from a compatible destination collection."""
    if not ids:
        return 0
    await validate_game_collection(client, collection_name=collection_name)
    await client.delete(
        collection_name=collection_name,
        points_selector=PointIdsList(points=ids),
        wait=True,
    )
    return len(ids)
