import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import gamerec.services.steam_ingestion as ingestion


@pytest.mark.asyncio
async def test_enrichment_respects_concurrency_and_continues_after_failure(
    monkeypatch,
):
    games = [
        SimpleNamespace(
            id=i,
            steam_app_id=appid,
            metadata_available=None,
            metadata_synced_at=None,
        )
        for i, appid in enumerate([10, 20, 30], start=1)
    ]

    # Fake database result containing our three games.
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: games)
    )

    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
    )

    active_workers = 0
    max_active_workers = 0
    started_appids = []

    two_workers_started = asyncio.Event()
    release_workers = asyncio.Event()

    async def fake_fetch_details(
        client: httpx.AsyncClient,
        steam_app_id: int,
    ) -> dict:
        nonlocal active_workers, max_active_workers

        active_workers += 1
        max_active_workers = max(
            max_active_workers,
            active_workers,
        )

        started_appids.append(steam_app_id)

        if len(started_appids) == 2:
            two_workers_started.set()

        try:
            # Hold workers here until the test releases them.
            await release_workers.wait()

            if steam_app_id == 10:
                request = httpx.Request(
                    "GET",
                    "https://example.test/appdetails",
                )

                response = httpx.Response(
                    429,
                    request=request,
                )

                # Simulate a request whose retries have been exhausted.
                raise httpx.HTTPStatusError(
                    "Too Many Requests",
                    request=request,
                    response=response,
                )

            return {
                "short_description": f"Game {steam_app_id}",
                "genres": [{"description": "Action"}],
            }

        finally:
            active_workers -= 1

    async def fake_fetch_reviews(
        client: httpx.AsyncClient,
        steam_app_id: int,
    ) -> dict:
        return {
            "review_score": 8,
            "review_score_desc": "Very Positive",
            "total_positive": 90,
            "total_negative": 10,
            "total_reviews": 100,
        }

    # Replace the real Steam integration functions.
    monkeypatch.setattr(
        ingestion,
        "fetch_steam_app_details",
        fake_fetch_details,
    )

    monkeypatch.setattr(
        ingestion,
        "fetch_steam_app_reviews",
        fake_fetch_reviews,
    )

    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(
            ingestion.get_steam_metadata(
                db=db,
                client=client,
                batch_size=3,
                max_games=3,
                request_delay=0,
                max_concurrent_requests=2,
            )
        )

        try:
            # Wait until two workers have started.
            await asyncio.wait_for(
                two_workers_started.wait(),
                timeout=3,
            )

            # Game 30 must not have started yet.
            assert started_appids == [10, 20]
            assert active_workers == 2

        finally:
            # Allow the workers to finish even if an assertion fails.
            release_workers.set()

        attempted = await asyncio.wait_for(task, timeout=3)

    # All three games were attempted.
    assert attempted == 3
    assert set(started_appids) == {10, 20, 30}

    # Never more than two active workers.
    assert max_active_workers == 2

    # Game 10 failed and remains eligible for a future run.
    assert games[0].metadata_available is None
    assert games[0].metadata_synced_at is None

    # Games 20 and 30 succeeded.
    for game in games[1:]:
        assert game.metadata_available is True
        assert game.metadata_synced_at is not None
        assert game.genres == ["Action"]
        assert game.total_reviews == 100

    # The batch was committed.
    db.commit.assert_awaited_once()