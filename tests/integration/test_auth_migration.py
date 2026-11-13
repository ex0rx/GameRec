"""The handwritten auth revision operates only on UUID-isolated PostgreSQL.

The fixture creates registered metadata. We remove only the new auth tables,
seed existing application data, then execute the revision's actual operations.
No historical migrations are replayed and no development data is accessed.
"""

import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, func, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from gamerec.db import Base
from gamerec.models import (
    AuthSession,
    Game,
    SteamLoginTransaction,
    User,
    UserGame,
    UserGamePreference,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER = "76561190000000001"
OLD_SYNC = datetime(2000, 1, 1, tzinfo=UTC)
AUTH_TABLES = {"auth_sessions", "steam_login_transactions"}
REVISION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations/versions/7c91f6a2d804_add_steam_authentication_tables.py"
)
spec = importlib.util.spec_from_file_location("auth_revision", REVISION_PATH)
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


async def migrate(sessions, direction):
    def apply(connection):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(revision, direction)()

    async with sessions.kw["bind"].begin() as connection:
        await connection.run_sync(apply)


async def schema(sessions):
    def describe(connection):
        inspector = inspect(connection)
        return {
            "tables": set(inspector.get_table_names()),
            "auth_columns": {
                table: inspector.get_columns(table) for table in AUTH_TABLES
            },
            "indexes": {table: inspector.get_indexes(table) for table in AUTH_TABLES},
            "unique": inspector.get_unique_constraints("steam_login_transactions"),
            "session_fk": inspector.get_foreign_keys("auth_sessions"),
            "metadata_diff": compare_metadata(
                MigrationContext.configure(connection), Base.metadata
            ),
        }

    async with sessions.kw["bind"].connect() as connection:
        return await connection.run_sync(describe)


async def seed_existing(sessions):
    async with sessions() as db, db.begin():
        db.add(User(steamid64=USER, library_last_synced_at=OLD_SYNC))
        db.add(Game(steam_app_id=10, name="Existing catalogue game"))
        await db.flush()
        db.add(
            UserGame(
                steamid64=USER,
                steam_app_id=10,
                playtime_forever_minutes=1200,
                playtime_2weeks_minutes=60,
            )
        )
        db.add(UserGamePreference(steamid64=USER, steam_app_id=10, preference="liked"))


async def existing_snapshot(sessions):
    async with sessions() as db:
        user = await db.get(User, USER)
        game = await db.scalar(select(Game).where(Game.steam_app_id == 10))
        owned = await db.get(UserGame, (USER, 10))
        preference = await db.get(UserGamePreference, (USER, 10))
        return (
            user.created_at,
            user.library_last_synced_at,
            game.name,
            owned.playtime_forever_minutes,
            owned.playtime_2weeks_minutes,
            preference.preference,
            preference.preference_last_updated_at,
        )


async def test_auth_revision_is_additive_reversible_and_matches_registered_models(
    pg_sessions,
):
    assert revision.down_revision == "2f86da100328"
    assert revision.revision == "7c91f6a2d804"
    before_schema = await schema(pg_sessions)
    assert AUTH_TABLES <= before_schema["tables"]
    assert {"sync_state", "steam_metadata_failures"} <= before_schema["tables"]
    await migrate(pg_sessions, "downgrade")
    await seed_existing(pg_sessions)
    before_data = await existing_snapshot(pg_sessions)

    await migrate(pg_sessions, "upgrade")
    upgraded = await schema(pg_sessions)
    assert upgraded["tables"] == before_schema["tables"]
    assert upgraded["metadata_diff"] == []
    assert await existing_snapshot(pg_sessions) == before_data
    assert {
        constraint["name"]: constraint["column_names"]
        for constraint in upgraded["unique"]
    } == {
        "uq_steam_login_browser_token_hash": ["browser_token_hash"],
        "uq_steam_login_nonce_hash": ["nonce_hash"],
    }
    assert upgraded["session_fk"][0]["referred_table"] == "users"
    assert upgraded["session_fk"][0]["options"]["ondelete"] == "CASCADE"
    for table in AUTH_TABLES:
        assert any(
            index["column_names"] == ["expires_at"]
            for index in upgraded["indexes"][table]
        )
        for column in upgraded["auth_columns"][table]:
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True

    await migrate(pg_sessions, "downgrade")
    async with pg_sessions.kw["bind"].connect() as connection:
        remaining = await connection.run_sync(
            lambda conn: set(inspect(conn).get_table_names())
        )
    assert remaining == before_schema["tables"] - AUTH_TABLES
    assert await existing_snapshot(pg_sessions) == before_data


async def test_migrated_auth_tables_enforce_hash_and_nonce_uniqueness(pg_sessions):
    await migrate(pg_sessions, "downgrade")
    await migrate(pg_sessions, "upgrade")
    await seed_existing(pg_sessions)
    now = datetime.now(UTC)
    raw_session = "synthetic-opaque-session-value"
    raw_browser = "synthetic-private-browser-value"
    session_hash = hashlib.sha256(raw_session.encode()).hexdigest()
    browser_hash = hashlib.sha256(raw_browser.encode()).hexdigest()
    async with pg_sessions() as db, db.begin():
        db.add(
            AuthSession(
                token_hash=session_hash,
                steamid64=USER,
                expires_at=now + timedelta(days=7),
            )
        )
        db.add_all(
            SteamLoginTransaction(
                login_id=letter * 43,
                browser_token_hash=browser_hash if letter == "a" else letter * 64,
                expected_return_to=f"http://localhost:5173/api/auth/steam/callback?login_id={letter * 43}",
                expires_at=now + timedelta(minutes=10),
            )
            for letter in ("a", "b")
        )
    async with pg_sessions() as db:
        session = await db.get(AuthSession, session_hash)
        transaction = await db.get(SteamLoginTransaction, "a" * 43)
        assert session.created_at.tzinfo is not None
        assert session.expires_at.tzinfo is not None
        assert transaction.created_at.tzinfo is not None
        assert raw_session not in vars(session).values()
        assert raw_browser not in vars(transaction).values()
        assert "token" not in AuthSession.__table__.columns
        assert "browser_token" not in SteamLoginTransaction.__table__.columns
        # Nullable nonce hashes must permit multiple pending transactions.
        assert (
            await db.scalar(select(func.count()).select_from(SteamLoginTransaction))
            == 2
        )

    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(
                    insert(AuthSession).values(
                        token_hash=session_hash,
                        steamid64=USER,
                        expires_at=now + timedelta(days=7),
                    )
                )
    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                db.add(
                    SteamLoginTransaction(
                        login_id="c" * 43,
                        browser_token_hash=browser_hash,
                        expected_return_to="http://localhost:5173/api/auth/steam/callback",
                        expires_at=now + timedelta(minutes=10),
                    )
                )
    async with pg_sessions() as db, db.begin():
        first = await db.get(SteamLoginTransaction, "a" * 43)
        first.nonce_hash = "f" * 64
        first.nonce_expires_at = now + timedelta(minutes=12)
    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                second = await db.get(SteamLoginTransaction, "b" * 43)
                second.nonce_hash = "f" * 64
                second.nonce_expires_at = now + timedelta(minutes=12)
    async with pg_sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                db.add(
                    AuthSession(
                        token_hash="d" * 64,
                        steamid64="76561190000000002",
                        expires_at=now + timedelta(days=7),
                    )
                )
    assert (await existing_snapshot(pg_sessions))[1:6] == (
        OLD_SYNC,
        "Existing catalogue game",
        1200,
        60,
        "liked",
    )


async def test_migrated_session_foreign_key_removes_only_deleted_users_sessions(
    pg_sessions,
):
    await migrate(pg_sessions, "downgrade")
    await migrate(pg_sessions, "upgrade")
    await seed_existing(pg_sessions)
    other = "76561190000000002"
    async with pg_sessions() as db, db.begin():
        db.add(User(steamid64=other))
        await db.flush()
        db.add_all(
            AuthSession(
                token_hash=letter * 64,
                steamid64=identity,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            for letter, identity in (("a", USER), ("b", other))
        )
    before = await existing_snapshot(pg_sessions)
    async with pg_sessions() as db, db.begin():
        await db.execute(delete(User).where(User.steamid64 == other))
    async with pg_sessions() as db:
        assert (await db.scalars(select(AuthSession.token_hash))).all() == ["a" * 64]
    assert await existing_snapshot(pg_sessions) == before
