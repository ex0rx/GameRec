import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from gamerec.services.steam_ingestion import get_steam_metadata


@pytest.mark.asyncio
async def test_enrichment_moves_to_next_game_after_retries(monkeypatch):
    first_game = SimpleNamespace(
        id=1,
        steam_app_id=10,
        metadata_available=None,
        metadata_synced_at=None,
    )

    second_game = SimpleNamespace(
        id=2,
        steam_app_id=20,
        metadata_available=None,
        metadata_synced_at=None,
    )

    # Simulate the DB returning one game per batch.
    def make_result(games):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: games,
            )
        )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                make_result([first_game]),
                make_result([second_game]),
            ]
        ),
        commit=AsyncMock(),
    )

    request_counts = {
        "first_details": 0,
        "second_details": 0,
        "second_reviews": 0,
    }

    def mock_steam(request: httpx.Request) -> httpx.Response:
        appid = request.url.params.get("appids")

        # Game 1: every app-details attempt fails.
        if "appdetails" in request.url.path and appid == "10":
            request_counts["first_details"] += 1
            return httpx.Response(429)

        # Game 2: app details succeed.
        if "appdetails" in request.url.path and appid == "20":
            request_counts["second_details"] += 1

            return httpx.Response(
                200,
                json={
                    "20": {
                        "success": True,
                        "data": {
                            "short_description": "Test game",
                            "genres": [
                                {"description": "Action"}
                            ],
                            "categories": [],
                            "developers": ["Test Developer"],
                            "publishers": ["Test Publisher"],
                            "is_free": False,
                            "release_date": {
                                "coming_soon": False,
                                "date": "10 Oct, 2007",
                            },
                            "header_image": "https://example.test/image.jpg",
                        },
                    }
                },
            )

        # Game 2: reviews succeed.
        if "/appreviews/20" in request.url.path:
            request_counts["second_reviews"] += 1

            return httpx.Response(
                200,
                json={
                    "success": 1,
                    "query_summary": {
                        "review_score": 8,
                        "review_score_desc": "Very Positive",
                        "total_positive": 90,
                        "total_negative": 10,
                        "total_reviews": 100,
                    },
                },
            )

        raise AssertionError(f"Unexpected request: {request.url}")

    # Skip actual backoff delays during the test.
    sleep_calls = []
    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock_steam),
        timeout=30,
    ) as client:
        attempted = await get_steam_metadata(
            db=db,
            client=client,
            batch_size=1,
            max_games=2,
        )

    # Both games were attempted, despite the first failing.
    assert attempted == 2

    # The first game exhausted all three HTTP attempts.
    assert request_counts["first_details"] == 3

    # The loop continued to the second game.
    assert request_counts["second_details"] == 1
    assert request_counts["second_reviews"] == 1

    # A failed game remains eligible for a future run.
    assert first_game.metadata_available is None
    assert first_game.metadata_synced_at is None

    # The next game was successfully enriched.
    assert second_game.metadata_available is True
    assert second_game.metadata_synced_at is not None
    assert second_game.short_description == "Test game"
    assert second_game.genres == ["Action"]
    assert second_game.total_reviews == 100

    # Each completed batch was committed.
    assert db.commit.await_count == 2

    # The retry delay was respected for each failed request.
    assert sleep_calls.count(0.5) == 2    