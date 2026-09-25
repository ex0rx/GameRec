"""Collection identity and safety boundaries without external services."""

from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

from gamerec.core.config import settings
from gamerec.services.vector_store import (
    delete_game_points,
    embedding_collection_metadata,
    ensure_game_collection,
    find_similar_games,
    upsert_game_points,
    validate_game_collection,
)
from gamerec.services.vector_sync import (
    EmbeddingSyncRecord,
    build_qdrant_points,
    sync_embeddings_to_qdrant,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "change", ["model", "revision", "size", "binding-size", "distance", "legacy"]
)
async def test_mismatch_rejects_all_entry_points(change, fake_db):
    client = AsyncQdrantClient(":memory:")
    metadata = embedding_collection_metadata()
    size = settings.embeddings_vector_size
    distance = Distance.COSINE
    if change == "model":
        metadata["gamerec_embeddings"]["model_name"] = "other"
    elif change == "revision":
        metadata["gamerec_embeddings"]["model_revision"] = "other"
    elif change == "size":
        size += 1
    elif change == "binding-size":
        metadata["gamerec_embeddings"]["vector_size"] += 1
    elif change == "distance":
        distance = Distance.DOT
    else:
        metadata = None
    try:
        await client.create_collection(
            settings.qdrant_game_collection,
            vectors_config=VectorParams(size=size, distance=distance),
            metadata=metadata,
        )
        point = build_qdrant_points(
            [EmbeddingSyncRecord(10, [1.0] * 384, "a", "Game", [], [])]
        )
        for operation in (
            lambda: ensure_game_collection(client),
            lambda: validate_game_collection(client),
            lambda: find_similar_games(client, 10),
            lambda: sync_embeddings_to_qdrant(fake_db, client),
            lambda: upsert_game_points(client, point),
            lambda: delete_game_points(
                client, [10], collection_name=settings.qdrant_game_collection
            ),
        ):
            with pytest.raises(ValueError, match="incompatible"):
                await operation()
        assert (await client.count(settings.qdrant_game_collection)).count == 0
        fake_db.execute.assert_not_awaited()
    finally:
        await client.close()


async def test_partial_sync_cannot_prune(fake_db):
    client = AsyncMock()
    with pytest.raises(ValueError, match="full sync"):
        await sync_embeddings_to_qdrant(
            fake_db, client, max_games=1, prune_missing=True
        )
    client.get_collection.assert_not_awaited()
    client.delete.assert_not_awaited()
