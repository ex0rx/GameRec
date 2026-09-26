"""Incremental updates, scoped pruning and version rebuilds on isolated stores."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct
from sqlalchemy import delete, select

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services.vector_store import ensure_game_collection, find_similar_games
from gamerec.services.vector_sync import (
    prune_embeddings_from_qdrant,
    sync_embeddings_to_qdrant,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_game_collection", "test-v1")
    c = AsyncQdrantClient(":memory:")
    try:
        await ensure_game_collection(c)
        yield c
    finally:
        await c.close()


async def seed(sessions):
    async with sessions() as db, db.begin():
        db.add_all([Game(steam_app_id=i, name=f"Game {i}") for i in [10, 30, 70]])
        await db.flush()
        db.add_all(
            [
                GameEmbedding(
                    steam_app_id=i,
                    model_name=settings.embeddings_model_name,
                    model_revision=settings.embeddings_model_revision,
                    input_hash="a" * 64,
                    embedding=[1.0, 0.0] + [0.0] * 382,
                )
                for i in [10, 30, 70]
            ]
        )


async def sync(sessions, client, **kwargs):
    async with sessions() as db:
        return await sync_embeddings_to_qdrant(db, client, batch_size=1, **kwargs)


async def all_points(client, name):
    points, _ = await client.scroll(name, limit=100, with_vectors=True)
    return {p.id: p for p in points}


async def test_incremental_hash_payload_and_new_points(
    pg_sessions, client, monkeypatch
):
    await seed(pg_sessions)
    first = await sync(pg_sessions, client)
    assert first == {
        "processed": 3,
        "inserted": 3,
        "updated": 0,
        "upserted": 3,
        "skipped": 0,
        "deleted": 0,
        "batches": 3,
    }
    spy = AsyncMock(wraps=client.upsert)
    monkeypatch.setattr(client, "upsert", spy)
    unchanged = await sync(pg_sessions, client)
    assert unchanged["skipped"] == 3 and unchanged["upserted"] == 0
    spy.assert_not_awaited()
    async with pg_sessions() as db, db.begin():
        row = await db.get(
            GameEmbedding,
            (10, settings.embeddings_model_name, settings.embeddings_model_revision),
        )
        row.input_hash = "b" * 64
        row.embedding = [0.0, 1.0] + [0.0] * 382
        game = await db.scalar(select(Game).where(Game.steam_app_id == 30))
        game.genres = ["RPG"]
    changed = await sync(pg_sessions, client)
    assert changed["updated"] == 2 and changed["skipped"] == 1
    points = await all_points(client, "test-v1")
    assert points[10].vector == [0.0, 1.0] + [0.0] * 382
    assert points[10].payload["input_hash"] == "b" * 64
    assert points[30].payload["genres"] == ["RPG"]


async def test_prune_preview_full_set_revision_isolation_and_scope(pg_sessions, client):
    await seed(pg_sessions)
    await sync(pg_sessions, client)
    await ensure_game_collection(client, collection_name="test-v2")
    await sync(pg_sessions, client, collection_name="test-v2")
    old = await all_points(client, "test-v1")
    async with pg_sessions() as db, db.begin():
        await db.execute(
            delete(GameEmbedding).where(GameEmbedding.steam_app_id.in_([10, 70]))
        )
        db.add(
            GameEmbedding(
                steam_app_id=10,
                model_name=settings.embeddings_model_name,
                model_revision="other",
                input_hash="c" * 64,
                embedding=[1.0] * 384,
            )
        )
        await db.execute(delete(Game).where(Game.steam_app_id == 70))
    async with pg_sessions() as db:
        preview = await prune_embeddings_from_qdrant(
            db, client, collection_name="test-v2", batch_size=1
        )
    assert preview == {"scanned": 3, "candidates": 2, "deleted": 0, "batches": 3}
    assert len(await all_points(client, "test-v2")) == 3
    result = await sync(
        pg_sessions, client, collection_name="test-v2", prune_missing=True
    )
    assert result["deleted"] == 2 and result["skipped"] == 1
    assert set(await all_points(client, "test-v2")) == {30}
    assert await all_points(client, "test-v1") == old
    # Repeat-safe and the source rows remain untouched.
    assert (
        await sync(pg_sessions, client, collection_name="test-v2", prune_missing=True)
    )["deleted"] == 0


async def test_bounded_sync_never_deletes_unvisited_points(pg_sessions, client):
    await seed(pg_sessions)
    await sync(pg_sessions, client)
    result = await sync(pg_sessions, client, max_games=1)
    assert result["processed"] == 1 and result["deleted"] == 0
    assert set(await all_points(client, "test-v1")) == {10, 30, 70}


async def test_empty_source_is_preview_only_unless_explicit(pg_sessions, client):
    await seed(pg_sessions)
    await sync(pg_sessions, client)
    async with pg_sessions() as db, db.begin():
        await db.execute(delete(GameEmbedding))
    async with pg_sessions() as db:
        assert (await prune_embeddings_from_qdrant(db, client))["candidates"] == 3
    assert len(await all_points(client, "test-v1")) == 3
    assert (await sync(pg_sessions, client, prune_missing=True))["deleted"] == 3
    assert await all_points(client, "test-v1") == {}


async def test_new_version_rebuild_preserves_active_collection(
    pg_sessions, client, monkeypatch
):
    await seed(pg_sessions)
    await sync(pg_sessions, client)
    old = await all_points(client, "test-v1")
    await ensure_game_collection(client, collection_name="test-v2")
    result = await sync(pg_sessions, client, collection_name="test-v2")
    assert result["inserted"] == 3
    assert await all_points(client, "test-v2") == old
    assert settings.qdrant_game_collection == "test-v1"
    assert len(await find_similar_games(client, 10)) == 2
    monkeypatch.setattr(settings, "embeddings_model_revision", "new-revision")
    async with pg_sessions() as db, db.begin():
        db.add(
            GameEmbedding(
                steam_app_id=10,
                model_name=settings.embeddings_model_name,
                model_revision="new-revision",
                input_hash="d" * 64,
                embedding=[0.0, 1.0] + [0.0] * 382,
            )
        )
    with pytest.raises(ValueError, match="binding"):
        await sync(pg_sessions, client)
    await ensure_game_collection(client, collection_name="test-v3")
    assert (await sync(pg_sessions, client, collection_name="test-v3"))["inserted"] == 1
    assert await all_points(client, "test-v1") == old
    assert await all_points(client, "test-v2") == old


async def test_failed_sync_never_prunes(pg_sessions, client, monkeypatch):
    await seed(pg_sessions)
    await client.upsert("test-v1", [PointStruct(id=999, vector=[1.0] * 384)])
    monkeypatch.setattr(client, "upsert", AsyncMock(side_effect=RuntimeError("failed")))
    delete_spy = AsyncMock(wraps=client.delete)
    monkeypatch.setattr(client, "delete", delete_spy)
    with pytest.raises(RuntimeError, match="failed"):
        await sync(pg_sessions, client, prune_missing=True)
    delete_spy.assert_not_awaited()
    assert set(await all_points(client, "test-v1")) == {999}


async def test_pruning_rejects_pending_source_changes(pg_sessions, client):
    async with pg_sessions() as db:
        db.add(Game(steam_app_id=1, name="Pending"))
        with pytest.raises(ValueError, match="clean session"):
            await prune_embeddings_from_qdrant(db, client, dry_run=False)


async def test_pruning_failure_rerun_recovers(pg_sessions, client, monkeypatch):
    await seed(pg_sessions)
    await sync(pg_sessions, client)
    async with pg_sessions() as db, db.begin():
        await db.execute(delete(GameEmbedding))
    original = client.delete
    calls = 0

    async def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("delete failed")
        return await original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(client, "delete", fail_second)
        async with pg_sessions() as db:
            with pytest.raises(RuntimeError, match="delete failed"):
                await prune_embeddings_from_qdrant(
                    db, client, batch_size=1, dry_run=False
                )
    assert set(await all_points(client, "test-v1")) == {30, 70}
    async with pg_sessions() as db:
        result = await prune_embeddings_from_qdrant(
            db, client, batch_size=1, dry_run=False
        )
    assert result["deleted"] == 2
    assert await all_points(client, "test-v1") == {}
