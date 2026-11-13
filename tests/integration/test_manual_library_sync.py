"""Manual-sync orchestration on a UUID-isolated PostgreSQL database.

Steam is mocked. Existing test_library_sync.py covers the saver/upsert's complete
rollback matrix; these checks exercise the new orchestration around that helper.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from gamerec.core.config import settings
from gamerec.models import Game, User, UserGame
from gamerec.services import manual_library_sync as sync

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER = "76561190000000001"
OLD = datetime(2000, 1, 1, tzinfo=UTC)
HTTP_CLIENT = httpx.AsyncClient


def payload(games):
    return {"response": {"game_count": len(games), "games": games}}


def game(appid=10, playtime=60):
    return {"appid": appid, "name": f"Synthetic {appid}", "playtime_forever": playtime}


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "manual_library_sync_enabled", True)
    monkeypatch.setattr(settings, "steamid64_test", USER)
    monkeypatch.setattr(
        sync.httpx,
        "AsyncClient",
        lambda **kwargs: HTTP_CLIENT(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)), **kwargs
        ),
    )
    fetch = AsyncMock(return_value=payload([game()]))
    metadata = AsyncMock(return_value=0)
    monkeypatch.setattr(sync, "fetch_steam_owned_games", fetch)
    monkeypatch.setattr(sync, "get_steam_metadata", metadata)
    return fetch, metadata


async def run_sync(sessions):
    async with sessions() as db:
        return await sync.sync_library(db, USER, sync.LibrarySyncCoordinator())


async def age_sync(sessions):
    async with sessions() as db:
        user = await db.get(User, USER)
        user.library_last_synced_at = OLD
        await db.commit()


async def snapshot(sessions):
    async with sessions() as db:
        user = await db.get(User, USER)
        rows = (
            await db.scalars(select(UserGame).order_by(UserGame.steam_app_id))
        ).all()
        return user.library_last_synced_at, [
            (
                row.steam_app_id,
                row.playtime_forever_minutes,
                row.playtime_2weeks_minutes,
            )
            for row in rows
        ]


async def test_unknown_allowed_account_repeat_updates_new_games_and_empty_preservation(
    pg_sessions, pipeline
):
    result = await run_sync(pg_sessions)
    assert (result.processed_games, result.total_games) == (1, 1)
    first_stamp, rows = await snapshot(pg_sessions)
    assert first_stamp == result.library_last_synced_at
    assert rows == [(10, 60, 0)]
    assert result.metadata_enrichment == "partial"
    for incoming in ([game(10, 120), game(20, 5)], [game(10, 120), game(20, 5)]):
        await age_sync(pg_sessions)
        pipeline[0].return_value = payload(incoming)
        result = await run_sync(pg_sessions)
        assert (result.processed_games, result.total_games) == (2, 2)
        assert (await snapshot(pg_sessions))[1] == [(10, 120, 0), (20, 5, 0)]
        async with pg_sessions() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1
            assert await db.scalar(select(func.count()).select_from(Game)) == 2
    await age_sync(pg_sessions)
    pipeline[0].return_value = payload([])
    result = await run_sync(pg_sessions)
    assert (result.processed_games, result.total_games) == (0, 2)
    assert result.library_last_synced_at > OLD
    assert result.metadata_enrichment == "complete"
    assert (await snapshot(pg_sessions))[1] == [(10, 120, 0), (20, 5, 0)]


@pytest.mark.parametrize(
    "failure", ["private", "malformed", "http", "duplicate", "missing-name", "overflow"]
)
async def test_core_failure_preserves_rows_and_timestamp(
    pg_sessions, pipeline, failure
):
    await run_sync(pg_sessions)
    await age_sync(pg_sessions)
    before = await snapshot(pg_sessions)
    if failure == "private":
        pipeline[0].return_value = {"response": {}}
    elif failure == "malformed":
        pipeline[0].return_value = {"response": {"game_count": 1, "games": []}}
    elif failure == "http":
        pipeline[0].side_effect = httpx.ConnectError("Synthetic connection failure")
    elif failure == "duplicate":
        pipeline[0].return_value = payload([game(10, 999), game(20), game(20)])
    elif failure == "missing-name":
        pipeline[0].return_value = payload([game(10, 999), game(20) | {"name": None}])
    else:
        pipeline[0].return_value = payload([game(10, 999), game(20, 2**40)])
    with pytest.raises((sync.LibrarySyncError, httpx.ConnectError)):
        await run_sync(pg_sessions)
    assert await snapshot(pg_sessions) == before
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Game)) == 1
        assert await db.scalar(select(func.count()).select_from(UserGame)) == 1


async def test_failed_metadata_transaction_rolls_back_without_undoing_import(
    pg_sessions, pipeline
):
    async def metadata(*, db, **kwargs):
        row = await db.scalar(select(Game).where(Game.steam_app_id == 10))
        row.name = "Uncommitted replacement"
        await db.flush()
        raise httpx.ConnectError("Synthetic metadata failure")

    pipeline[1].side_effect = metadata
    result = await run_sync(pg_sessions)
    assert result.metadata_enrichment == "partial"
    async with pg_sessions() as db:
        assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60
        assert (
            await db.get(User, USER)
        ).library_last_synced_at == result.library_last_synced_at
        row = await db.scalar(select(Game).where(Game.steam_app_id == 10))
        assert row.name == "Synthetic 10"
        assert row.metadata_synced_at is None
