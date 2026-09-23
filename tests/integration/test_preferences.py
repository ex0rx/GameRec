"""Preference routes and integrity checks against isolated PostgreSQL."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from gamerec.models.game import Game
from gamerec.models.user import User
from gamerec.models.user_game import UserGame
from gamerec.models.user_game_preference import UserGamePreference
from gamerec.services.steam_library import save_steam_library

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER_A = "synthetic-preferences-a"
USER_B = "synthetic-preferences-b"
OLD_TIMESTAMP = datetime(2000, 1, 1, tzinfo=UTC)


async def seed_catalogue(pg_sessions, *, users=(USER_A,), app_ids=(10,)):
    async with pg_sessions() as db:
        db.add_all(User(steamid64=user) for user in users)
        db.add_all(Game(steam_app_id=appid, name=f"Game {appid}") for appid in app_ids)
        await db.commit()


async def test_put_creates_then_updates_one_persisted_preference(
    pg_client, pg_sessions
):
    await seed_catalogue(pg_sessions)
    url = f"/users/{USER_A}/preferences/10"
    created = await pg_client.put(url, json={"preference": "liked"})
    assert created.status_code == 200
    body = created.json()
    assert body.keys() == {
        "steamid64",
        "steam_app_id",
        "preference",
        "preference_last_updated_at",
    }
    assert body["steamid64"] == USER_A
    assert body["steam_app_id"] == 10
    assert body["preference"] == "liked"
    timestamp = datetime.fromisoformat(body["preference_last_updated_at"])
    assert timestamp.tzinfo is not None
    async with pg_sessions() as db:
        saved = await db.get(UserGamePreference, (USER_A, 10))
        assert saved.preference == "liked"
        assert saved.preference_last_updated_at == timestamp
        saved.preference_last_updated_at = OLD_TIMESTAMP
        await db.commit()

    for value in ("disliked", "neutral", "liked"):
        updated = await pg_client.put(url, json={"preference": value})
        assert updated.status_code == 200
        assert updated.json()["preference"] == value
        async with pg_sessions() as db:
            assert (
                await db.scalar(select(func.count()).select_from(UserGamePreference))
                == 1
            )
            saved = await db.get(UserGamePreference, (USER_A, 10))
            assert saved.preference == value
            assert saved.preference_last_updated_at > OLD_TIMESTAMP
            assert saved.preference_last_updated_at == datetime.fromisoformat(
                updated.json()["preference_last_updated_at"]
            )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"preference": "loved"},
        {"preference": "LIKED"},
        {"preference": None},
        {"preference": 1},
    ],
)
async def test_invalid_preference_returns_422_without_changing_state(
    pg_client, pg_sessions, payload
):
    await seed_catalogue(pg_sessions)
    url = f"/users/{USER_A}/preferences/10"
    created = await pg_client.put(url, json={"preference": "liked"})
    assert created.status_code == 200
    response = await pg_client.put(url, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "preference"]
    async with pg_sessions() as db:
        saved = await db.get(UserGamePreference, (USER_A, 10))
        assert saved.preference == "liked"
        assert saved.preference_last_updated_at == datetime.fromisoformat(
            created.json()["preference_last_updated_at"]
        )


@pytest.mark.parametrize("method", ["put", "get", "delete"])
async def test_unknown_user_returns_404(pg_client, pg_sessions, method):
    await seed_catalogue(pg_sessions)
    url = "/users/synthetic-missing/preferences"
    kwargs = {}
    if method != "get":
        url += "/10"
    if method == "put":
        kwargs["json"] = {"preference": "liked"}
    response = await pg_client.request(method, url, **kwargs)
    assert response.status_code == 404
    assert response.json() == {
        "detail": "User with steamid64 synthetic-missing not found"
    }
    async with pg_sessions() as db:
        assert await db.get(User, "synthetic-missing") is None
        assert (
            await db.scalar(select(func.count()).select_from(UserGamePreference)) == 0
        )


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_unknown_game_returns_404(pg_client, pg_sessions, method):
    await seed_catalogue(pg_sessions)
    kwargs = {"json": {"preference": "liked"}} if method == "put" else {}
    response = await pg_client.request(
        method, f"/users/{USER_A}/preferences/999", **kwargs
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Game with steam_app_id 999 not found"}
    async with pg_sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(UserGamePreference)) == 0
        )
        assert await db.scalar(select(func.count()).select_from(Game)) == 1


async def test_existing_user_without_preferences_returns_empty_list(
    pg_client, pg_sessions
):
    await seed_catalogue(pg_sessions)
    response = await pg_client.get(f"/users/{USER_A}/preferences")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "query, field",
    [("limit=0", "limit"), ("limit=101", "limit"), ("offset=-1", "offset")],
)
async def test_invalid_pagination_returns_422(pg_client, query, field):
    response = await pg_client.get(f"/users/{USER_A}/preferences?{query}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", field]


async def test_get_paginates_by_app_id_and_isolates_users(pg_client, pg_sessions):
    await seed_catalogue(pg_sessions, users=(USER_A, USER_B), app_ids=range(1, 66))
    async with pg_sessions() as db:
        db.add_all(
            UserGamePreference(steamid64=USER_A, steam_app_id=appid, preference="liked")
            for appid in reversed(range(1, 66))
        )
        db.add(
            UserGamePreference(steamid64=USER_B, steam_app_id=10, preference="disliked")
        )
        await db.commit()

    for query, expected in (
        ("", list(range(1, 51))),
        ("?limit=50&offset=50", list(range(51, 66))),
        ("?limit=100&offset=0", list(range(1, 66))),
        ("?limit=1&offset=64", [65]),
        ("?limit=50&offset=65", []),
        ("?limit=50&offset=999", []),
        ("", list(range(1, 51))),
    ):
        response = await pg_client.get(f"/users/{USER_A}/preferences{query}")
        assert response.status_code == 200
        assert [item["steam_app_id"] for item in response.json()] == expected
        assert all(item["steamid64"] == USER_A for item in response.json())
        assert all(item["preference"] == "liked" for item in response.json())
    response = await pg_client.get(f"/users/{USER_B}/preferences")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["steamid64"] == USER_B
    assert response.json()[0]["steam_app_id"] == 10
    assert response.json()[0]["preference"] == "disliked"


async def test_delete_is_idempotent_and_preserves_other_preferences(
    pg_client, pg_sessions
):
    await seed_catalogue(pg_sessions, users=(USER_A, USER_B), app_ids=(10, 20))
    async with pg_sessions() as db:
        db.add_all(
            UserGamePreference(steamid64=user, steam_app_id=appid, preference="liked")
            for user, appid in ((USER_A, 10), (USER_A, 20), (USER_B, 10))
        )
        await db.commit()
    response = await pg_client.put(
        f"/users/{USER_A}/preferences/10", json={"preference": "disliked"}
    )
    assert response.status_code == 200
    async with pg_sessions() as db:
        assert (await db.get(UserGamePreference, (USER_A, 10))).preference == "disliked"
        assert (await db.get(UserGamePreference, (USER_A, 20))).preference == "liked"
        assert (await db.get(UserGamePreference, (USER_B, 10))).preference == "liked"
    for _ in range(2):
        response = await pg_client.delete(f"/users/{USER_A}/preferences/10")
        assert response.status_code == 204
        assert response.content == b""
        async with pg_sessions() as db:
            rows = (await db.scalars(select(UserGamePreference))).all()
            assert {(row.steamid64, row.steam_app_id) for row in rows} == {
                (USER_A, 20),
                (USER_B, 10),
            }
            assert all(row.preference == "liked" for row in rows)


async def test_database_rejects_duplicate_composite_key(pg_client, pg_sessions):
    await seed_catalogue(pg_sessions)
    response = await pg_client.put(
        f"/users/{USER_A}/preferences/10", json={"preference": "liked"}
    )
    assert response.status_code == 200
    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(
                    insert(UserGamePreference).values(
                        steamid64=USER_A, steam_app_id=10, preference="neutral"
                    )
                )
    async with pg_sessions() as db:
        rows = (await db.scalars(select(UserGamePreference))).all()
        assert len(rows) == 1
        assert rows[0].preference == "liked"


async def test_preferences_do_not_change_ownership_or_playtime(pg_client, pg_sessions):
    await seed_catalogue(pg_sessions, app_ids=(10, 20))
    async with pg_sessions() as db:
        db.add(
            UserGame(
                steamid64=USER_A,
                steam_app_id=10,
                playtime_forever_minutes=60,
                playtime_2weeks_minutes=5,
            )
        )
        user = await db.get(User, USER_A)
        user.library_last_synced_at = OLD_TIMESTAMP
        await db.commit()

    # A preference is allowed even for a catalogue game the user does not own.
    for appid in (10, 20):
        for value in ("liked", "disliked", "neutral"):
            response = await pg_client.put(
                f"/users/{USER_A}/preferences/{appid}", json={"preference": value}
            )
            assert response.status_code == 200
            async with pg_sessions() as db:
                owned = (await db.scalars(select(UserGame))).all()
                assert [
                    (
                        row.steam_app_id,
                        row.playtime_forever_minutes,
                        row.playtime_2weeks_minutes,
                    )
                    for row in owned
                ] == [(10, 60, 5)]
                assert (
                    await db.get(User, USER_A)
                ).library_last_synced_at == OLD_TIMESTAMP
        response = await pg_client.delete(f"/users/{USER_A}/preferences/{appid}")
        assert response.status_code == 204
    async with pg_sessions() as db:
        owned = (await db.scalars(select(UserGame))).all()
        assert [
            (
                row.steam_app_id,
                row.playtime_forever_minutes,
                row.playtime_2weeks_minutes,
            )
            for row in owned
        ] == [(10, 60, 5)]
        assert (await db.get(User, USER_A)).library_last_synced_at == OLD_TIMESTAMP


async def test_library_resync_preserves_explicit_preferences(pg_client, pg_sessions):
    await seed_catalogue(pg_sessions, app_ids=(10, 20))
    async with pg_sessions() as db:
        await save_steam_library(
            db,
            USER_A,
            [
                {
                    "steam_app_id": 10,
                    "playtime_forever_minutes": 60,
                    "playtime_2weeks_minutes": 5,
                }
            ],
        )
    for appid, value in ((10, "disliked"), (20, "liked")):
        response = await pg_client.put(
            f"/users/{USER_A}/preferences/{appid}", json={"preference": value}
        )
        assert response.status_code == 200
    before = await pg_client.get(f"/users/{USER_A}/preferences")
    assert before.status_code == 200
    async with pg_sessions() as db:
        await save_steam_library(
            db,
            USER_A,
            [
                {
                    "steam_app_id": 10,
                    "playtime_forever_minutes": 120,
                    "playtime_2weeks_minutes": 15,
                }
            ],
        )
    after = await pg_client.get(f"/users/{USER_A}/preferences")
    assert after.status_code == 200
    assert after.json() == before.json()
    async with pg_sessions() as db:
        owned = (await db.scalars(select(UserGame))).all()
        assert [
            (
                row.steam_app_id,
                row.playtime_forever_minutes,
                row.playtime_2weeks_minutes,
            )
            for row in owned
        ] == [(10, 120, 15)]
