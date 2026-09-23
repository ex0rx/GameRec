"""Safe test defaults and reusable unit fixtures (no application DB access)."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Set before application imports. Integration tests use a separate explicit URL.
os.environ.update(
    DATABASE_URL="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
    STEAM_API_KEY="synthetic-key",
    STEAMID64_TEST="synthetic-user",
)


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run PostgreSQL tests; requires TEST_POSTGRES_ADMIN_URL",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(
            reason="Pass --run-integration for isolated PostgreSQL tests"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail(
            "Live HTTP is forbidden in tests; use MockTransport or ASGITransport"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def fake_db():
    db = Mock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=list))
    db.commit = AsyncMock()
    transaction = AsyncMock()
    transaction.__aexit__.return_value = False
    db.begin = Mock(return_value=transaction)
    return db
