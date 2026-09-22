"""Phase 4C unit tests: fake sessions and mocked HTTP, no database required.

Assertions cover SQL intent, orchestration, and transaction-context usage.
They do not verify PostgreSQL persistence, uniqueness, or actual rollback.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.models import Game
from gamerec.scripts import get_steam_owned_games as script
from gamerec.services import steam_ingestion as ingestion
from gamerec.services.steam_ingestion import get_steam_metadata
from gamerec.services.steam_library import save_steam_library, upsert_user_game_library

pytestmark = pytest.mark.asyncio
USER = "synthetic-user"


def owned_game(appid=10, minutes=60, name="Example Game"):
    return {
        "steam_app_id": appid,
        "name": name,
        "playtime_forever_minutes": minutes,
        "playtime_2weeks_minutes": 10,
    }


def sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def test_repeated_import_builds_conflict_updates_with_latest_playtime(fake_db):
    # Simulate a missing catalogue entry, then an existing one on the next import.
    fake_db.scalars.side_effect = [
        SimpleNamespace(all=list),
        SimpleNamespace(all=lambda: [10]),
    ]
    for minutes in (60, 120):
        fake_db.execute.reset_mock()
        before = datetime.now(tz=UTC)
        assert (
            await save_steam_library(fake_db, USER, [owned_game(minutes=minutes)]) == 1
        )
        statements = [call.args[0] for call in fake_db.execute.await_args_list]
        assert "ON CONFLICT (steamid64) DO NOTHING" in sql(statements[0])
        ownership = next(s for s in statements if s.table.name == "user_games")
        assert "ON CONFLICT (steamid64, steam_app_id) DO UPDATE" in sql(ownership)
        assert "playtime_forever_minutes = excluded.playtime_forever_minutes" in sql(
            ownership
        )
        assert "playtime_2weeks_minutes = excluded.playtime_2weeks_minutes" in sql(
            ownership
        )
        params = ownership.compile().params
        assert params["playtime_forever_minutes_m0"] == minutes
        assert params["steam_app_id_m0"] == 10
        assert params["steamid64_m0"] == USER
        catalogue = [s for s in statements if s.table.name == "games"]
        assert len(catalogue) == (1 if minutes == 60 else 0)
        if catalogue:
            assert "ON CONFLICT (steam_app_id) DO NOTHING" in sql(catalogue[0])
        timestamp = statements[-1].compile().params["library_last_synced_at"]
        assert before <= timestamp <= datetime.now(tz=UTC)
        assert f"WHERE users.steamid64 = '{USER}'" in sql(statements[-1])
    assert fake_db.begin.call_count == 2
    assert fake_db.begin.return_value.__aexit__.await_count == 2
    fake_db.begin.return_value.__aexit__.assert_awaited_with(None, None, None)


async def test_upsert_existing_catalogue_game_issues_no_game_writes(fake_db):
    fake_db.scalars.return_value = SimpleNamespace(all=lambda: [10])
    assert (
        await upsert_user_game_library(
            fake_db, USER, [owned_game(name="Different API name")]
        )
        == 1
    )
    statements = [call.args[0] for call in fake_db.execute.await_args_list]
    assert [s.table.name for s in statements] == ["users", "user_games"]
    query = fake_db.scalars.await_args.args[0]
    assert "games.steam_app_id IN (10)" in sql(query)
    fake_db.begin.assert_not_called()
    fake_db.commit.assert_not_awaited()


async def test_missing_name_exits_transaction_with_error_before_game_writes(fake_db):
    with pytest.raises(ValueError, match="non-empty string") as exc:
        await save_steam_library(
            fake_db, USER, [owned_game(), owned_game(20, name=None)]
        )
    # SQLAlchemy owns rollback; prove the error reaches its transaction context.
    exit_args = fake_db.begin.return_value.__aexit__.await_args.args
    assert exit_args[0] is ValueError
    assert exit_args[1] is exc.value
    fake_db.execute.assert_awaited_once()
    assert fake_db.execute.await_args.args[0].table.name == "users"


async def test_empty_library_only_upserts_user_and_updates_timestamp(fake_db):
    before = datetime.now(tz=UTC)
    assert await save_steam_library(fake_db, USER, []) == 0
    statements = [call.args[0] for call in fake_db.execute.await_args_list]
    assert len(statements) == 2
    assert "INSERT INTO users" in sql(statements[0])
    assert "UPDATE users" in sql(statements[1])
    assert (
        before
        <= statements[1].compile().params["library_last_synced_at"]
        <= datetime.now(tz=UTC)
    )
    # No ownership DELETE or other ownership write is issued.
    assert all(s.table.name == "users" for s in statements)
    fake_db.scalars.assert_not_awaited()
    fake_db.begin.return_value.__aexit__.assert_awaited_once_with(None, None, None)


async def test_duplicate_app_ids_reach_transaction_error_path(fake_db):
    with pytest.raises(ValueError, match="Duplicate app IDs"):
        await save_steam_library(fake_db, USER, [owned_game(), owned_game()])
    fake_db.scalars.assert_not_awaited()
    assert fake_db.begin.return_value.__aexit__.await_args.args[0] is ValueError


async def test_unavailable_response_skips_saving_and_enrichment(monkeypatch):
    monkeypatch.setattr(settings, "steamid64_test", USER)
    monkeypatch.setattr(settings, "steam_api_key", "synthetic-key")
    save = AsyncMock()
    enrich = AsyncMock()
    sessions = Mock(
        side_effect=AssertionError("Unavailable library opened a DB session")
    )
    monkeypatch.setattr(script, "save_steam_library", save)
    monkeypatch.setattr(script, "get_steam_metadata", enrich)
    monkeypatch.setattr(script, "SessionLocal", sessions)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        assert request.url.path == "/IPlayerService/GetOwnedGames/v1/"
        return httpx.Response(200, json={"response": {}})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    await script.main()
    assert len(requests) == 1
    save.assert_not_called()
    enrich.assert_not_called()
    sessions.assert_not_called()


async def test_enrichment_builds_app_filter_and_processes_returned_game(
    fake_db, monkeypatch
):
    game = Game(id=2, steam_app_id=20, name="Twenty")
    fake_db.execute.side_effect = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [game])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=list)),
    ]
    clear = AsyncMock()
    monkeypatch.setattr(ingestion, "clear_metadata_failure", clear)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/api/appdetails":
            assert request.url.params["appids"] == "20"
            return httpx.Response(
                200,
                json={
                    "20": {
                        "success": True,
                        "data": {
                            "short_description": "Enriched twenty",
                            "is_free": False,
                        },
                    }
                },
            )
        assert request.url.path == "/appreviews/20"
        return httpx.Response(
            200, json={"success": 1, "query_summary": {"total_reviews": 42}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert (
            await get_steam_metadata(
                fake_db,
                client,
                steam_app_ids=[20],
                batch_size=1,
                max_games=3,
                request_delay=0,
            )
            == 1
        )

    # Inspect the SQL as well as the mocked result: removing the filter must fail.
    assert fake_db.execute.await_count == 2
    for call in fake_db.execute.await_args_list:
        query = sql(call.args[0])
        assert "games.steam_app_id IN (20)" in query
        assert "games.metadata_synced_at IS NULL" in query
    assert requests == ["/api/appdetails", "/appreviews/20"]
    assert game.short_description == "Enriched twenty"
    assert game.total_reviews == 42
    assert game.metadata_available is True
    assert game.metadata_synced_at is not None
    clear.assert_awaited_once_with(db=fake_db, steam_app_id=20)
    fake_db.commit.assert_awaited_once()


async def test_empty_enrichment_filter_does_no_database_or_http_work():
    db = AsyncMock(spec=AsyncSession)

    def unexpected_request(request):
        pytest.fail("Empty filter made an HTTP request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        assert await get_steam_metadata(db, client, steam_app_ids=[]) == 0
    assert db.mock_calls == []
