"""Real commits in a fresh, UUID-named PostgreSQL database per test.

Only TEST_POSTGRES_ADMIN_URL is used. The named admin DB must already exist
on a dedicated test server; neither DATABASE_URL nor development volumes are used.
Model metadata defines the test schema; these fixtures do not replay migrations.
"""

import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import gamerec.models  # noqa: F401 -- register all tables
from gamerec.db import Base, get_db
from gamerec.main import app


@pytest_asyncio.fixture
async def pg_sessions():
    configured = os.environ.get("TEST_POSTGRES_ADMIN_URL")
    if not configured:
        pytest.fail("--run-integration requires TEST_POSTGRES_ADMIN_URL")
    url = make_url(configured)
    if url.drivername != "postgresql+asyncpg" or url.database != "gamerec_test_admin":
        pytest.fail(
            "Use a dedicated postgresql+asyncpg test server /gamerec_test_admin"
        )
    name = f"gamerec_test_{uuid4().hex}"
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    engine = create_async_engine(url.set(database=name), poolclass=NullPool)
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execute(text(f'DROP DATABASE "{name}"'))
        finally:
            await admin.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_sessions, monkeypatch):
    async def override_db():
        async with pg_sessions() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
