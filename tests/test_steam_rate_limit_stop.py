from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import gamerec.services.steam_ingestion as ingestion


def make_429_error(appid: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET",
        f"https://example.test/appdetails?appids={appid}",
    )

    response = httpx.Response(
        429,
        request=request,
    )

    return httpx.HTTPStatusError(
        "Too Many Requests",
        request=request,
        response=response,
    )


def make_db_result(games):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            all=lambda: games,
        )
    )


async def run_rate_limit_scenario(
    monkeypatch,
    outcomes: list[str],
):
    games = [
        SimpleNamespace(
            id=index,
            steam_app_id=1000 + index,
            metadata_available=None,
            metadata_synced_at=None,
        )
        for index in range(1, len(outcomes) + 1)
    ]

    # Each app ID has a predetermined outcome.
    outcomes_by_appid = {
        game.steam_app_id: outcome
        for game, outcome in zip(games, outcomes, strict=True)
    }

    # One game per database batch.
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[make_db_result([game]) for game in games]),
        commit=AsyncMock(),
    )

    # Prevent failure-tracking helpers from consuming
    # additional mocked db.execute() results.
    record_failure = AsyncMock()
    clear_failure = AsyncMock()

    monkeypatch.setattr(
        ingestion,
        "record_metadata_failure",
        record_failure,
    )

    monkeypatch.setattr(
        ingestion,
        "clear_metadata_failure",
        clear_failure,
    )

    attempted_appids = []

    async def fake_fetch_one_game(
        client,
        steam_app_id,
        semaphore,
        request_delay,
    ):
        attempted_appids.append(steam_app_id)

        outcome = outcomes_by_appid[steam_app_id]

        if outcome == "429":
            return (
                None,
                None,
                make_429_error(steam_app_id),
            )

        # Minimal valid app-details response.
        return (
            {
                "short_description": "Test game",
                "genres": [],
                "categories": [],
                "developers": [],
                "publishers": [],
                "release_date": {
                    "coming_soon": True,
                    "date": "",
                },
                "is_free": False,
                "header_image": None,
            },
            None,
            None,
        )

    monkeypatch.setattr(
        ingestion,
        "fetch_one_game",
        fake_fetch_one_game,
    )

    async with httpx.AsyncClient() as client:
        attempted = await ingestion.get_steam_metadata(
            db=db,
            client=client,
            batch_size=1,
            max_games=len(outcomes),
            max_concurrent_requests=1,
            request_delay=0,
            max_consecutive_rate_limits=3,
        )

    return (
        attempted,
        db,
        games,
        record_failure,
        clear_failure,
        attempted_appids,
    )


@pytest.mark.asyncio
async def test_stops_after_three_consecutive_429s(
    monkeypatch,
    capsys,
):
    (
        attempted,
        db,
        games,
        record,
        clear,
        attempted_appids,
    ) = await run_rate_limit_scenario(
        monkeypatch,
        ["429", "429", "429", "success", "success"],
    )

    # Only the first three games should be attempted.
    assert attempted_appids == [1001, 1002, 1003]
    assert attempted == 3

    # No fourth database batch should be selected.
    assert db.execute.await_count == 3
    assert db.commit.await_count == 3

    # All three HTTP failures should be recorded.
    assert record.await_count == 3
    clear.assert_not_awaited()

    # Failed games remain eligible for a future run.
    for game in games[:3]:
        assert game.metadata_synced_at is None

    # Games 4 and 5 were never attempted.
    for game in games[3:]:
        assert game.metadata_synced_at is None

    output = capsys.readouterr().out
    assert "Reason: rate limit" in output


@pytest.mark.asyncio
async def test_success_resets_consecutive_429_counter(
    monkeypatch,
    capsys,
):
    (
        attempted,
        db,
        games,
        record,
        clear,
        attempted_appids,
    ) = await run_rate_limit_scenario(
        monkeypatch,
        ["429", "429", "success", "429", "429"],
    )

    # The success at game 3 resets the counter,
    # so all five games should be attempted.
    assert attempted_appids == [1001, 1002, 1003, 1004, 1005]
    assert attempted == 5

    assert db.execute.await_count == 5
    assert db.commit.await_count == 5

    assert record.await_count == 4
    clear.assert_awaited_once()

    # The third game was successfully enriched.
    assert games[2].metadata_available is True
    assert games[2].metadata_synced_at is not None

    output = capsys.readouterr().out
    assert "Reason: rate limit" not in output
