"""Exercise the real HTTP route with a fake DB dependency and service.

Pagination/isolation checks prove request forwarding and response contracts;
they do not test database query execution. No PostgreSQL or Steam calls occur.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, call

import httpx
import pytest
import pytest_asyncio

from gamerec.api import users
from gamerec.db import get_db
from gamerec.main import app

pytestmark = pytest.mark.asyncio
USER_A = "synthetic-user-a"
USER_B = "synthetic-user-b"


def game(appid):
    return {
        "steam_app_id": appid,
        "name": f"Synthetic game {appid}",
        "playtime_forever_minutes": appid * 60,
        "playtime_2weeks_minutes": appid * 5,
    }


def library(user=USER_A, games=None, *, timestamp=None, limit=50, offset=0):
    games = [] if games is None else games
    return {
        "steamid64": user,
        "library_last_synced_at": timestamp,
        "total_games": len(games),
        "limit": limit,
        "offset": offset,
        "games": games[offset : offset + limit],
    }


@pytest.fixture
def library_service(monkeypatch):
    service = AsyncMock(spec=users.get_user_library)
    monkeypatch.setattr(users, "get_user_library", service)
    return service


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, library_service):
    async def override_db():
        yield fake_db

    # monkeypatch restores any previous override after the test.
    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    assert fake_db.mock_calls == []


async def test_pagination_returns_remaining_games_without_page_overlap(
    client, library_service, fake_db
):
    games = [game(appid) for appid in range(1, 66)]

    async def paginated_response(*, db, steamid64, limit, offset):
        return library(steamid64, games, limit=limit, offset=offset)

    library_service.side_effect = paginated_response
    first = await client.get(f"/users/{USER_A}/library?limit=50&offset=0")
    second = await client.get(f"/users/{USER_A}/library?limit=50&offset=50")

    assert first.status_code == second.status_code == 200
    first_page, second_page = first.json(), second.json()
    assert first_page == library(games=games)
    assert second_page == library(games=games, offset=50)
    assert len(first_page["games"]) == 50
    assert len(second_page["games"]) == 15
    assert second_page["total_games"] == 65
    assert {g["steam_app_id"] for g in first_page["games"]}.isdisjoint(
        g["steam_app_id"] for g in second_page["games"]
    )
    assert library_service.await_args_list == [
        call(db=fake_db, steamid64=USER_A, limit=50, offset=0),
        call(db=fake_db, steamid64=USER_A, limit=50, offset=50),
    ]


async def test_unknown_user_returns_404(client, library_service, fake_db):
    library_service.return_value = None
    response = await client.get("/users/synthetic-unknown/library")
    assert response.status_code == 404
    assert response.json() == {
        "detail": "User with steamid64 synthetic-unknown not found",
    }
    library_service.assert_awaited_once_with(
        db=fake_db, steamid64="synthetic-unknown", limit=50, offset=0
    )


async def test_existing_user_with_empty_library_returns_200(client, library_service):
    library_service.return_value = library()
    response = await client.get(f"/users/{USER_A}/library")
    assert response.status_code == 200
    assert response.json() == library()
    assert response.json()["games"] == []
    assert response.json()["total_games"] == 0


@pytest.mark.parametrize(
    "query, field",
    [("limit=0", "limit"), ("limit=101", "limit"), ("offset=-1", "offset")],
)
async def test_invalid_pagination_returns_422_without_calling_service(
    client, library_service, query, field
):
    response = await client.get(f"/users/{USER_A}/library?{query}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", field]
    library_service.assert_not_called()


async def test_user_id_is_forwarded_and_libraries_remain_separate(
    client, library_service, fake_db
):
    libraries = {USER_A: [game(10)], USER_B: [game(20), game(30)]}

    async def user_response(*, db, steamid64, limit, offset):
        return library(steamid64, libraries[steamid64], limit=limit, offset=offset)

    library_service.side_effect = user_response
    for user in (USER_A, USER_B, USER_A):
        response = await client.get(f"/users/{user}/library")
        assert response.status_code == 200
        assert response.json() == library(user, libraries[user])
    assert library_service.await_args_list == [
        call(db=fake_db, steamid64=user, limit=50, offset=0)
        for user in (USER_A, USER_B, USER_A)
    ]


@pytest.mark.parametrize(
    "timestamp, expected",
    [(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "2026-01-02T03:04:05Z"), (None, None)],
)
async def test_sync_timestamp_serialization(
    client, library_service, timestamp, expected
):
    library_service.return_value = library(timestamp=timestamp)
    response = await client.get(f"/users/{USER_A}/library")
    assert response.status_code == 200
    assert response.json()["library_last_synced_at"] == expected


@pytest.mark.parametrize("lifetime, recent", [(None, 10), (60, None), (None, None)])
async def test_nullable_stored_playtimes_are_serialized(
    client, library_service, lifetime, recent
):
    entry = game(10) | {
        "playtime_forever_minutes": lifetime,
        "playtime_2weeks_minutes": recent,
    }
    library_service.return_value = library(games=[entry])
    response = await client.get(f"/users/{USER_A}/library")
    assert response.status_code == 200
    assert response.json()["games"] == [entry]


async def test_invalid_service_payload_is_rejected_by_response_validation(
    client, library_service
):
    library_service.return_value = library(
        games=[game(10) | {"steam_app_id": "invalid"}]
    )
    response = await client.get(f"/users/{USER_A}/library")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
