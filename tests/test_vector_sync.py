"""Pure conversion and async boundaries, with no external services."""

from unittest.mock import AsyncMock

import pytest

from gamerec.core.config import settings
from gamerec.services import vector_store, vector_sync
from gamerec.services.vector_sync import EmbeddingSyncRecord, build_qdrant_points


def record(**changes):
    values = {
        "steam_app_id": 123,
        "embedding": [1.0] + [0.0] * 383,
        "input_hash": "a" * 64,
        "name": "Example",
        "genres": ["Adventure"],
        "categories": ["Single-player"],
    }
    values.update(changes)
    return EmbeddingSyncRecord(**values)


def test_points_preserve_ids_vectors_and_payload_without_mutating_source():
    source = record()
    (point,) = build_qdrant_points([source])
    assert point.id == 123
    assert point.vector == source.embedding
    assert len(point.vector) == 384
    assert point.payload == {
        "steam_app_id": 123,
        "name": "Example",
        "genres": ["Adventure"],
        "categories": ["Single-player"],
        "input_hash": "a" * 64,
    }
    point.payload["genres"].append("RPG")
    point.vector[0] = 0.0
    assert source.genres == ["Adventure"]
    assert source.embedding[0] == 1.0


def test_nullable_metadata_and_empty_batch():
    (point,) = build_qdrant_points([record(name=None, genres=None, categories=None)])
    assert point.payload["name"] == ""
    assert point.payload["genres"] == point.payload["categories"] == []
    assert build_qdrant_points([]) == []


@pytest.mark.parametrize("size", [0, 383, 385])
def test_invalid_dimensions(size):
    with pytest.raises(ValueError, match="Invalid embedding dimensions for 123"):
        build_qdrant_points([record(embedding=[0.0] * size)])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"batch_size": 0}, {"batch_size": -1}, {"max_games": 0}, {"max_games": -1}],
)
async def test_invalid_limits_do_no_io(fake_db, kwargs):
    client = AsyncMock()
    with pytest.raises(ValueError):
        await vector_sync.sync_embeddings_to_qdrant(fake_db, client, **kwargs)
    fake_db.execute.assert_not_awaited()
    client.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_fetch_batch_size(fake_db):
    with pytest.raises(ValueError):
        await vector_sync.get_embedding_sync_batch(fake_db, batch_size=0)
    fake_db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_waits_and_uses_configured_collection(
    monkeypatch, compatible_collection_info
):
    monkeypatch.setattr(settings, "qdrant_game_collection", "test-points")
    client = AsyncMock()
    client.get_collection.return_value = compatible_collection_info
    assert await vector_store.upsert_game_points(client, []) == 0
    client.upsert.assert_not_awaited()
    points = build_qdrant_points([record()])
    assert await vector_store.upsert_game_points(client, points) == 1
    client.upsert.assert_awaited_once_with(
        collection_name="test-points", points=points, wait=True
    )
    client.upsert.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        await vector_store.upsert_game_points(client, points)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_collection_setup_awaits_and_preserves_injected_client(
    exists, compatible_collection_info
):
    client = AsyncMock()
    client.get_collection.return_value = compatible_collection_info
    client.collection_exists.return_value = exists
    await vector_store.ensure_game_collection(client)
    client.collection_exists.assert_awaited_once_with(settings.qdrant_game_collection)
    assert client.create_collection.await_count == int(not exists)
    client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_collection_setup_closes_owned_client_on_error(monkeypatch):
    client = AsyncMock()
    client.collection_exists.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(vector_store, "get_qdrant_client", lambda: client)
    with pytest.raises(RuntimeError, match="unavailable"):
        await vector_store.ensure_game_collection()
    client.close.assert_awaited_once()
