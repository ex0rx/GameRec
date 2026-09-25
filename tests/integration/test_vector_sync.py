"""Real disposable PostgreSQL -> isolated Qdrant local engine; no model loads."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services import vector_sync
from gamerec.services.vector_store import ensure_game_collection

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def qdrant(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_game_collection", "isolated-test-embeddings")
    client = AsyncQdrantClient(":memory:")
    try:
        await ensure_game_collection(client)
        yield client
    finally:
        await client.close()


async def seed(pg_sessions):
    async with pg_sessions() as db, db.begin():
        # PK ordering deliberately differs from sparse app-ID ordering.
        db.add_all(
            [
                Game(
                    id=i,
                    steam_app_id=appid,
                    name=f"Game {appid}",
                    genres=["RPG"] if appid == 10 else None,
                    categories=None,
                )
                for i, appid in enumerate([90, 10, 55, 30, 70, 120, 130, 140], 1)
            ]
        )
        await db.flush()
        for appid in [90, 10, 55, 30, 70, 120, 130]:
            db.add(
                GameEmbedding(
                    steam_app_id=appid,
                    model_name="other"
                    if appid == 120
                    else settings.embeddings_model_name,
                    model_revision="other"
                    if appid == 130
                    else settings.embeddings_model_revision,
                    input_hash="a" * 64,
                    embedding=[1.0] + [0.0] * 383,
                )
            )
        # Same app ID in another revision must not duplicate or replace its point.
        db.add(
            GameEmbedding(
                steam_app_id=10,
                model_name=settings.embeddings_model_name,
                model_revision="old",
                input_hash="b" * 64,
                embedding=[0.0, 1.0] + [0.0] * 382,
            )
        )


async def points(client):
    result, cursor = await client.scroll(
        settings.qdrant_game_collection, limit=100, with_vectors=True
    )
    assert cursor is None
    return {point.id: point for point in result}


async def test_cursor_filtering_and_payload(pg_sessions, qdrant):
    await seed(pg_sessions)
    async with pg_sessions() as db:
        first = await vector_sync.get_embedding_sync_batch(db, batch_size=2)
        second = await vector_sync.get_embedding_sync_batch(
            db, first[-1].steam_app_id, 2
        )
        third = await vector_sync.get_embedding_sync_batch(
            db, second[-1].steam_app_id, 2
        )
        assert [r.steam_app_id for r in first] == [10, 30]
        assert [r.steam_app_id for r in second] == [55, 70]
        assert [r.steam_app_id for r in third] == [90]
        assert await vector_sync.get_embedding_sync_batch(db, 90, 2) == []
        assert await vector_sync.sync_embeddings_to_qdrant(
            db, qdrant, batch_size=2
        ) == {
            "processed": 5,
            "upserted": 5,
            "batches": 3,
            "inserted": 5,
            "updated": 0,
            "skipped": 0,
            "deleted": 0,
        }
    saved = await points(qdrant)
    assert set(saved) == {10, 30, 55, 70, 90}
    for appid, point in saved.items():
        assert len(point.vector) == 384
        assert point.vector == [1.0] + [0.0] * 383
        assert point.payload == {
            "steam_app_id": appid,
            "name": f"Game {appid}",
            "genres": ["RPG"] if appid == 10 else [],
            "categories": [],
            "input_hash": "a" * 64,
        }


async def test_bounded_batches_repeat_runs_and_updates(
    pg_sessions, qdrant, monkeypatch
):
    await seed(pg_sessions)
    spy = AsyncMock(wraps=qdrant.upsert)
    monkeypatch.setattr(qdrant, "upsert", spy)
    for run in range(2):
        async with pg_sessions() as db:
            assert await vector_sync.sync_embeddings_to_qdrant(
                db, qdrant, batch_size=2, max_games=3
            ) == {
                "processed": 3,
                "upserted": 3 if run == 0 else 0,
                "batches": 2,
                "inserted": 3 if run == 0 else 0,
                "updated": 0,
                "skipped": 0 if run == 0 else 3,
                "deleted": 0,
            }
    assert [[p.id for p in c.kwargs["points"]] for c in spy.await_args_list] == [
        [10, 30],
        [55],
    ]
    assert set(await points(qdrant)) == {10, 30, 55}
    async with pg_sessions() as db, db.begin():
        row = await db.get(
            GameEmbedding,
            (10, settings.embeddings_model_name, settings.embeddings_model_revision),
        )
        row.embedding = [0.0, 1.0] + [0.0] * 382
        row.input_hash = "c" * 64
    async with pg_sessions() as db:
        assert await vector_sync.sync_embeddings_to_qdrant(
            db, qdrant, batch_size=2, max_games=99
        ) == {
            "processed": 5,
            "upserted": 3,
            "batches": 3,
            "inserted": 2,
            "updated": 1,
            "skipped": 2,
            "deleted": 0,
        }
    saved = await points(qdrant)
    assert len(saved) == 5
    assert saved[10].vector == [0.0, 1.0] + [0.0] * 382
    assert saved[10].payload["input_hash"] == "c" * 64
    await ensure_game_collection(qdrant)
    assert len(await points(qdrant)) == 5


async def test_empty_sync(pg_sessions, qdrant):
    async with pg_sessions() as db:
        assert await vector_sync.sync_embeddings_to_qdrant(db, qdrant) == {
            "processed": 0,
            "upserted": 0,
            "batches": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "deleted": 0,
        }
    assert await points(qdrant) == {}


async def test_failure_stops_batches_and_rerun_recovers(
    pg_sessions, qdrant, monkeypatch
):
    await seed(pg_sessions)
    original = qdrant.upsert
    calls = []

    async def fail_second(**kwargs):
        calls.append([p.id for p in kwargs["points"]])
        if len(calls) == 2:
            raise RuntimeError("injected failure")
        return await original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(qdrant, "upsert", fail_second)
        async with pg_sessions() as db:
            with pytest.raises(RuntimeError, match="injected failure"):
                await vector_sync.sync_embeddings_to_qdrant(db, qdrant, batch_size=2)
    assert calls == [[10, 30], [55, 70]]
    assert set(await points(qdrant)) == {10, 30}
    async with pg_sessions() as db:
        assert await vector_sync.sync_embeddings_to_qdrant(
            db, qdrant, batch_size=2
        ) == {
            "processed": 5,
            "upserted": 3,
            "batches": 3,
            "inserted": 3,
            "updated": 0,
            "skipped": 2,
            "deleted": 0,
        }
    assert len(await points(qdrant)) == 5
