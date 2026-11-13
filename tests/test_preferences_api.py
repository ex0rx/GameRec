"""Preference HTTP contracts with mocked persistence; no external I/O."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import users
from gamerec.core.config import settings
from gamerec.db import get_db
from gamerec.main import app

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"
OTHER = "76561190000000002"


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "steamid64_test", USER)
    saved = SimpleNamespace(
        steamid64=USER,
        steam_app_id=10,
        preference="liked",
        preference_last_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    put = AsyncMock(return_value=saved)
    delete = AsyncMock()
    monkeypatch.setattr(users, "upsert_user_game_preference", put)
    monkeypatch.setattr(users, "delete_user_game_preference", delete)
    return put, delete


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, services, authenticated_user):
    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"gamerec_session": "s" * 43},
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        yield client


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize("configured_account", [None, "invalid", OTHER])
async def test_owner_writes_do_not_depend_on_development_allowlist(
    client, services, monkeypatch, method, configured_account
):
    monkeypatch.setattr(settings, "steamid64_test", configured_account)
    kwargs = {"json": {"preference": "liked"}} if method == "put" else {}
    response = await client.request(method, f"/users/{USER}/preferences/10", **kwargs)
    assert response.status_code == (200 if method == "put" else 204)
    services[0 if method == "put" else 1].assert_awaited_once()


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize(
    "identity", ["123", "７" * 17, "a" * 17, USER + "0", USER + "%0A"]
)
async def test_invalid_write_identity_is_422(client, services, identity, method):
    kwargs = {"json": {"preference": "liked"}} if method == "put" else {}
    response = await client.request(
        method, f"/users/{identity}/preferences/10", **kwargs
    )
    assert response.status_code == 422
    for service in services:
        service.assert_not_called()


@pytest.mark.parametrize("value", [None, "unrated", "LIKED", 1])
async def test_invalid_values_are_422(client, services, value):
    response = await client.put(
        f"/users/{USER}/preferences/10", json={"preference": value}
    )
    assert response.status_code == 422
    services[0].assert_not_called()


async def test_put_and_delete_reuse_contract_for_authenticated_owner(
    client, fake_db, services
):
    response = await client.put(
        f"/users/{USER}/preferences/10", json={"preference": "liked"}
    )
    assert response.status_code == 200
    assert response.json()["preference"] == "liked"
    services[0].assert_awaited_once_with(
        db=fake_db, steamid64=USER, steam_app_id=10, preference="liked"
    )
    response = await client.delete(f"/users/{USER}/preferences/10")
    assert response.status_code == 204
    assert response.content == b""
    services[1].assert_awaited_once_with(db=fake_db, steamid64=USER, steam_app_id=10)


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_storage_errors_are_safe_503(client, services, method):
    services[0 if method == "put" else 1].side_effect = SQLAlchemyError(
        "secret SQL and credentials"
    )
    kwargs = {"json": {"preference": "liked"}} if method == "put" else {}
    response = await client.request(method, f"/users/{USER}/preferences/10", **kwargs)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert "secret" not in response.text and USER not in response.text


@pytest.mark.parametrize("appid", [0, -1, 2**31, "not-an-id"])
async def test_invalid_app_id_is_422(client, services, appid):
    response = await client.put(
        f"/users/{USER}/preferences/{appid}", json={"preference": "liked"}
    )
    assert response.status_code == 422
    services[0].assert_not_called()


async def test_library_capabilities_follow_authenticated_owner_policy(
    client, monkeypatch
):
    monkeypatch.setattr(
        users,
        "get_user_library",
        AsyncMock(
            return_value={
                "steamid64": USER,
                "library_last_synced_at": None,
                "total_games": 0,
                "limit": 50,
                "offset": 0,
                "games": [],
            }
        ),
    )
    response = await client.get(f"/users/{USER}/library")
    assert response.json()["preference_writes_allowed"] is True
    assert response.json()["sync_allowed"] is True
    monkeypatch.setattr(settings, "environment", "production")
    assert (await client.get(f"/users/{USER}/library")).json()[
        "preference_writes_allowed"
    ] is True


@pytest.mark.parametrize("preference", ["liked", "disliked", "neutral"])
async def test_all_existing_preference_values_are_forwarded_unchanged(
    client, services, fake_db, preference
):
    services[0].return_value.preference = preference
    response = await client.put(
        f"/users/{USER}/preferences/10", json={"preference": preference}
    )
    assert response.status_code == 200
    assert response.json()["preference"] == preference
    services[0].assert_awaited_once_with(
        db=fake_db, steamid64=USER, steam_app_id=10, preference=preference
    )
