"""Exercise the real library route, service, joins and PostgreSQL ordering."""

from datetime import UTC, datetime

import pytest

from gamerec.models import User
from gamerec.services.steam_library import save_steam_library

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER_A = "76561190000000001"
USER_B = "76561190000000002"


async def test_library_pages_are_stably_ordered_and_scoped(
    pg_sessions, pg_client, authenticate_pg_client
):
    # Reverse insertion order proves the explicit ORDER BY, not insertion order.
    games = [
        {
            "steam_app_id": appid,
            "name": f"Game {appid}",
            "playtime_forever_minutes": appid,
            "playtime_2weeks_minutes": None,
            "preference": None,
        }
        for appid in range(65, 0, -1)
    ]
    async with pg_sessions() as db:
        await save_steam_library(db, USER_A, games)
    async with pg_sessions() as db:
        await save_steam_library(
            db, USER_B, [games[0] | {"playtime_forever_minutes": 999}]
        )
    timestamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    async with pg_sessions() as db:
        user = await db.get(User, USER_A)
        user.library_last_synced_at = timestamp
        await db.commit()

    await authenticate_pg_client(USER_A)
    for limit, offset in [(50, 0), (50, 50), (100, 0), (1, 64), (50, 65), (50, 100)]:
        response = await pg_client.get(
            f"/users/{USER_A}/library", params={"limit": limit, "offset": offset}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total_games"] == 65
        assert (body["limit"], body["offset"]) == (limit, offset)
        expected = list(range(1, 66))[offset : offset + limit]
        assert [g["steam_app_id"] for g in body["games"]] == expected
        assert [g["playtime_forever_minutes"] for g in body["games"]] == expected
        assert all(g["playtime_2weeks_minutes"] is None for g in body["games"])
        assert body["library_last_synced_at"] == "2026-01-02T03:04:05Z"
        assert (
            await pg_client.get(
                f"/users/{USER_A}/library", params={"limit": limit, "offset": offset}
            )
        ).json() == body

    await authenticate_pg_client(USER_B)
    response = await pg_client.get(f"/users/{USER_B}/library")
    assert response.status_code == 200
    assert response.json()["total_games"] == 1
    assert response.json()["games"] == [
        {
            "steam_app_id": 65,
            "name": "Game 65",
            "playtime_forever_minutes": 999,
            "playtime_2weeks_minutes": None,
            "preference": None,
        }
    ]


async def test_anonymous_and_existing_empty_user_are_distinct(
    pg_sessions, pg_client, authenticate_pg_client
):
    response = await pg_client.get(f"/users/{USER_A}/library")
    assert response.status_code == 401
    async with pg_sessions() as db:
        db.add(User(steamid64=USER_A))
        await db.commit()
    await authenticate_pg_client(USER_A)
    response = await pg_client.get(f"/users/{USER_A}/library")
    assert response.status_code == 200
    assert response.json() == {
        "steamid64": USER_A,
        "sync_allowed": True,
        "preference_writes_allowed": True,
        "games": [],
        "total_games": 0,
        "limit": 50,
        "offset": 0,
        "library_last_synced_at": None,
    }
