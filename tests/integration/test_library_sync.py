"""Persistence checks complementing the existing mocked sync/HTTP contracts."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from gamerec.core.config import settings
from gamerec.integrations import steam
from gamerec.models import Game, SteamMetadataFailure, User, UserGame
from gamerec.scripts import get_steam_owned_games as script
from gamerec.services.steam_library import save_steam_library, upsert_user_game_library

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER = "synthetic-library-user"
OLD_SYNC = datetime(2000, 1, 1, tzinfo=UTC)


def owned(appid=10, lifetime=60, recent=10, name="Synthetic game"):
    return {
        "steam_app_id": appid,
        "name": name,
        "playtime_forever_minutes": lifetime,
        "playtime_2weeks_minutes": recent,
    }


async def seed_library(sessions):
    async with sessions() as db:
        await save_steam_library(db, USER, [owned()])
    async with sessions() as db:
        user = await db.get(User, USER)
        user.library_last_synced_at = OLD_SYNC
        await db.commit()


def mock_script_http(monkeypatch, sessions, respond):
    monkeypatch.setattr(script, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "steamid64_test", USER)
    client_type = httpx.AsyncClient

    def client(**kwargs):
        # Pacing/retry timing has dedicated unit tests; no real HTTP is sent here.
        kwargs.pop("event_hooks", None)
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(script.httpx, "AsyncClient", client)


async def test_first_sync_repeat_and_missing_optional_playtime(pg_sessions):
    for lifetime, recent in [(60, 10), (120, 30), (None, None)]:
        async with pg_sessions() as db:
            assert (
                await save_steam_library(
                    db, USER, [owned(lifetime=lifetime, recent=recent)]
                )
                == 1
            )
        async with pg_sessions() as db:
            for model in (User, Game, UserGame):
                assert await db.scalar(select(func.count()).select_from(model)) == 1
            row = await db.get(UserGame, (USER, 10))
            assert (row.playtime_forever_minutes, row.playtime_2weeks_minutes) == (
                lifetime,
                recent,
            )
            assert (await db.get(User, USER)).library_last_synced_at is not None


async def test_upsert_requires_caller_commit_and_preserves_catalogue(pg_sessions):
    async with pg_sessions() as db:
        game = Game(
            steam_app_id=10,
            name="Catalogue name",
            genres=["Strategy"],
            total_reviews=123,
        )
        db.add(game)
        await db.commit()
        original = {c.key: getattr(game, c.key) for c in Game.__table__.columns}
    async with pg_sessions() as db:
        assert await upsert_user_game_library(db, USER, [owned()]) == 1
        # Closing without commit must discard the helper's changes.
    async with pg_sessions() as db:
        assert await db.get(User, USER) is None
    async with pg_sessions() as db, db.begin():
        await upsert_user_game_library(db, USER, [owned(name="Incoming name")])
    async with pg_sessions() as db:
        game = await db.scalar(select(Game).where(Game.steam_app_id == 10))
        assert {c.key: getattr(game, c.key) for c in Game.__table__.columns} == original
        assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60
        assert (await db.get(User, USER)).library_last_synced_at is None


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["missing-name", "duplicate", "database-error"])
async def test_failed_sync_rolls_back_all_writes(pg_sessions, existing, failure):
    if existing:
        await seed_library(pg_sessions)
    games = [owned(lifetime=999), owned(20), owned(30)]
    error = ValueError
    if failure == "missing-name":
        games[-1]["name"] = None
    elif failure == "duplicate":
        games.append(owned(20))
    else:
        # PostgreSQL integer overflow happens after user/catalogue INSERTs.
        games[-1]["playtime_forever_minutes"] = 2**40
        error = DBAPIError
    async with pg_sessions() as db:
        with pytest.raises(error):
            await save_steam_library(db, USER, games)
        assert not db.in_transaction()
    async with pg_sessions() as db:
        for model in (User, Game, UserGame):
            assert await db.scalar(select(func.count()).select_from(model)) == int(
                existing
            )
        if existing:
            assert (await db.get(User, USER)).library_last_synced_at == OLD_SYNC
            assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60


@pytest.mark.parametrize("existing", [False, True])
async def test_explicit_empty_response_commits_success_without_deleting_ownership(
    pg_sessions, monkeypatch, existing
):
    if existing:
        await seed_library(pg_sessions)

    def respond(request):
        assert request.url.path == "/IPlayerService/GetOwnedGames/v1/"
        return httpx.Response(200, json={"response": {"game_count": 0}})

    mock_script_http(monkeypatch, pg_sessions, respond)
    await script.main()
    async with pg_sessions() as db:
        assert (await db.get(User, USER)).library_last_synced_at > OLD_SYNC
        assert await db.scalar(select(func.count()).select_from(UserGame)) == int(
            existing
        )
        if existing:
            assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60


@pytest.mark.parametrize("case", ["inaccessible", "malformed", "forbidden"])
async def test_unusable_steam_response_preserves_last_success(
    pg_sessions, monkeypatch, case
):
    await seed_library(pg_sessions)

    def respond(request):
        assert request.url.path == "/IPlayerService/GetOwnedGames/v1/"
        if case == "forbidden":
            return httpx.Response(403)
        payload = {} if case == "inaccessible" else {"game_count": 1, "games": []}
        return httpx.Response(200, json={"response": payload})

    mock_script_http(monkeypatch, pg_sessions, respond)
    if case == "inaccessible":
        await script.main()
    else:
        with pytest.raises(
            ValueError if case == "malformed" else httpx.HTTPStatusError
        ):
            await script.main()
    async with pg_sessions() as db:
        assert (await db.get(User, USER)).library_last_synced_at == OLD_SYNC
        assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60
        assert await db.scalar(select(func.count()).select_from(UserGame)) == 1


async def test_enrichment_failure_keeps_successful_library_import(
    pg_sessions, monkeypatch
):
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/IPlayerService/GetOwnedGames/v1/":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "game_count": 1,
                        "games": [
                            {"appid": 10, "name": "Synthetic", "playtime_forever": 60},
                        ],
                    }
                },
            )
        assert request.url.path == "/api/appdetails"
        return httpx.Response(403)

    mock_script_http(monkeypatch, pg_sessions, respond)
    # Avoid the worker's post-request delay; HTTP retry behavior is tested elsewhere.
    monkeypatch.setattr(steam.asyncio, "sleep", AsyncMock())
    await script.main()
    assert requests == ["/IPlayerService/GetOwnedGames/v1/", "/api/appdetails"]
    async with pg_sessions() as db:
        assert (await db.get(UserGame, (USER, 10))).playtime_forever_minutes == 60
        assert (await db.get(User, USER)).library_last_synced_at is not None
        game = await db.scalar(select(Game).where(Game.steam_app_id == 10))
        assert game.metadata_synced_at is None
        assert game.metadata_available is None
        assert (await db.get(SteamMetadataFailure, 10)).attempt_count == 1
