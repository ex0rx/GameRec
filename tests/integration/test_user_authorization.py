"""Owner boundaries using real PostgreSQL sessions and persisted account data."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from gamerec.api import recommendations, users
from gamerec.core.auth_security import token_hash
from gamerec.models import AuthSession, Game, User, UserGame, UserGamePreference
from gamerec.services import manual_library_sync as sync

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
OWNER = "76561190000000001"
OTHER = "76561190000000002"
ORIGIN = "http://localhost:5173"
OLD = datetime(2000, 1, 1, tzinfo=UTC)
OPERATIONS = ["library", "preferences", "recommendations", "sync", "put", "delete"]


async def seed(sessions):
    async with sessions() as db:
        db.add_all(
            User(steamid64=value, library_last_synced_at=OLD)
            for value in (OWNER, OTHER)
        )
        db.add(Game(steam_app_id=10, name="Synthetic game"))
        await db.commit()
        for identity in (OWNER, OTHER):
            db.add(
                UserGame(
                    steamid64=identity, steam_app_id=10, playtime_forever_minutes=60
                )
            )
            db.add(
                UserGamePreference(
                    steamid64=identity, steam_app_id=10, preference="neutral"
                )
            )
        await db.commit()


async def snapshot(sessions):
    async with sessions() as db:
        owned = (
            await db.execute(
                select(
                    UserGame.steamid64,
                    UserGame.steam_app_id,
                    UserGame.playtime_forever_minutes,
                    UserGame.playtime_2weeks_minutes,
                ).order_by(UserGame.steamid64)
            )
        ).all()
        preferences = (
            await db.execute(
                select(
                    UserGamePreference.steamid64,
                    UserGamePreference.steam_app_id,
                    UserGamePreference.preference,
                    UserGamePreference.preference_last_updated_at,
                ).order_by(UserGamePreference.steamid64)
            )
        ).all()
        timestamps = (
            await db.execute(
                select(User.steamid64, User.library_last_synced_at).order_by(
                    User.steamid64
                )
            )
        ).all()
        return owned, preferences, timestamps


async def request_operation(client, operation, identity=OWNER, **kwargs):
    root = f"/users/{identity}"
    if operation == "library":
        return await client.get(root + "/library", **kwargs)
    if operation == "preferences":
        return await client.get(root + "/preferences", **kwargs)
    if operation == "recommendations":
        return await client.post(
            "/recommendations", json={"steamid64": identity}, **kwargs
        )
    if operation == "sync":
        return await client.post(root + "/library/sync", **kwargs)
    if operation == "put":
        return await client.put(
            root + "/preferences/10", json={"preference": "liked"}, **kwargs
        )
    return await client.delete(root + "/preferences/10", **kwargs)


def forbid_expensive_work(monkeypatch):
    importer, ranker = AsyncMock(), AsyncMock()
    monkeypatch.setattr(users, "sync_library", importer)
    monkeypatch.setattr(recommendations, "get_user_recommendations", ranker)
    return importer, ranker


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_anonymous_account_operations_leave_data_unchanged(
    pg_sessions, pg_client, authenticate_pg_client, monkeypatch, operation
):
    await seed(pg_sessions)
    before = await snapshot(pg_sessions)
    importer, ranker = forbid_expensive_work(monkeypatch)
    response = await request_operation(pg_client, operation)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"
    assert_private(response)
    importer.assert_not_called()
    ranker.assert_not_called()
    assert await snapshot(pg_sessions) == before


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_other_account_is_rejected_before_private_reads_or_external_work(
    pg_sessions, pg_client, authenticate_pg_client, monkeypatch, operation
):
    await seed(pg_sessions)
    await authenticate_pg_client(OWNER)
    before = await snapshot(pg_sessions)
    importer, ranker = forbid_expensive_work(monkeypatch)
    response = await request_operation(pg_client, operation, OTHER)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "account_forbidden"
    assert OWNER not in response.text and OTHER not in response.text
    assert_private(response)
    importer.assert_not_called()
    ranker.assert_not_called()
    assert await snapshot(pg_sessions) == before


@pytest.mark.parametrize("operation", ["sync", "put", "delete"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://untrusted.invalid"},
        {"Referer": "https://untrusted.invalid/page"},
        {"Origin": "https://untrusted.invalid", "Referer": ORIGIN + "/library"},
    ],
)
async def test_csrf_rejection_preserves_account_data(
    pg_sessions, pg_client, authenticate_pg_client, monkeypatch, operation, headers
):
    await seed(pg_sessions)
    await authenticate_pg_client(OWNER)
    pg_client.headers.pop("Origin")
    before = await snapshot(pg_sessions)
    importer, _ = forbid_expensive_work(monkeypatch)
    response = await request_operation(pg_client, operation, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_failed"
    importer.assert_not_called()
    assert await snapshot(pg_sessions) == before


@pytest.mark.parametrize("state", ["expired", "revoked", "malformed", "duplicate"])
async def test_invalid_session_cannot_read_or_write(
    pg_sessions, pg_client, authenticate_pg_client, state
):
    await seed(pg_sessions)
    raw = await authenticate_pg_client(OWNER)
    async with pg_sessions() as db:
        if state == "expired":
            session = await db.get(AuthSession, token_hash(raw))
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "revoked":
            await db.execute(
                delete(AuthSession).where(AuthSession.token_hash == token_hash(raw))
            )
        await db.commit()
    headers = {}
    if state == "malformed":
        headers["Cookie"] = "gamerec_session=bad"
    elif state == "duplicate":
        headers["Cookie"] = f"gamerec_session={raw}; gamerec_session={raw}"
    before = await snapshot(pg_sessions)
    for operation in ("library", "put", "delete"):
        response = await request_operation(pg_client, operation, headers=headers)
        assert response.status_code == 401
    assert await snapshot(pg_sessions) == before


async def test_real_auth_transaction_finishes_before_preference_writes_and_clear(
    pg_sessions, pg_client, authenticate_pg_client
):
    await seed(pg_sessions)
    await authenticate_pg_client(OWNER)
    baseline = await snapshot(pg_sessions)
    # Referer-only requests retain the existing CSRF fallback.
    pg_client.headers.pop("Origin")
    response = await request_operation(
        pg_client, "put", headers={"Referer": ORIGIN + "/library"}
    )
    assert response.status_code == 200
    listed = await request_operation(pg_client, "preferences")
    assert [row["preference"] for row in listed.json()] == ["liked"]
    cleared = await request_operation(
        pg_client, "delete", headers={"Referer": ORIGIN + "/library"}
    )
    assert cleared.status_code == 204
    assert (await request_operation(pg_client, "preferences")).json() == []
    after = await snapshot(pg_sessions)
    assert after[0] == baseline[0] and after[2] == baseline[2]
    assert len(after[1]) == 1 and after[1][0][0] == OTHER


async def test_real_auth_then_sync_commits_and_immediate_cooldown_avoids_fetch(
    pg_sessions, pg_client, authenticate_pg_client, monkeypatch
):
    await seed(pg_sessions)
    await authenticate_pg_client(OWNER)
    coordinator = sync.LibrarySyncCoordinator()

    async def current_coordinator():
        return coordinator

    from gamerec.main import app

    monkeypatch.setitem(
        app.dependency_overrides, users.get_sync_coordinator, current_coordinator
    )
    fetch = AsyncMock(
        return_value={
            "response": {
                "game_count": 1,
                "games": [
                    {"appid": 10, "name": "Synthetic game", "playtime_forever": 120}
                ],
            }
        }
    )
    monkeypatch.setattr(sync, "fetch_steam_owned_games", fetch)
    monkeypatch.setattr(sync, "get_steam_metadata", AsyncMock(return_value=0))
    response = await request_operation(pg_client, "sync")
    assert response.status_code == 200
    assert response.json()["processed_games"] == 1
    async with pg_sessions() as db:
        assert (await db.get(UserGame, (OWNER, 10))).playtime_forever_minutes == 120
        assert (await db.get(UserGame, (OTHER, 10))).playtime_forever_minutes == 60
        assert (await db.get(User, OWNER)).library_last_synced_at > OLD
        assert (await db.get(UserGamePreference, (OWNER, 10))).preference == "neutral"
    repeated = await request_operation(pg_client, "sync")
    assert repeated.status_code == 429
    assert int(repeated.headers["Retry-After"]) > 0
    fetch.assert_awaited_once()
    assert coordinator.pending == set()
