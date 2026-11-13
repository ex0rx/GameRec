"""Account authorization through real FastAPI session and CSRF dependencies."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import recommendations, users
from gamerec.core.config import settings
from gamerec.db import get_db
from gamerec.main import app
from gamerec.services.manual_library_sync import LibrarySyncCoordinator

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"
OTHER = "76561190000000002"
ORIGIN = "http://localhost:5173"
SESSION = "s" * 43
OPERATIONS = ["library", "preferences", "recommendations", "sync", "put", "delete"]
WRITES = ["sync", "put", "delete"]


async def request(client, operation, *, identity=USER, headers=None):
    route = f"/users/{identity}"
    if operation == "library":
        return await client.get(route + "/library", headers=headers)
    if operation == "preferences":
        return await client.get(route + "/preferences", headers=headers)
    if operation == "recommendations":
        return await client.post(
            "/recommendations", json={"steamid64": identity}, headers=headers
        )
    if operation == "sync":
        return await client.post(route + "/library/sync", headers=headers)
    if operation == "put":
        return await client.put(
            route + "/preferences/10", json={"preference": "liked"}, headers=headers
        )
    return await client.delete(route + "/preferences/10", headers=headers)


@pytest.fixture
def services(monkeypatch):
    saved = SimpleNamespace(
        steamid64=USER,
        steam_app_id=10,
        preference="liked",
        preference_last_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    values = {
        "get_user_library": {
            "steamid64": USER,
            "library_last_synced_at": None,
            "total_games": 0,
            "limit": 50,
            "offset": 0,
            "games": [],
        },
        "get_user_game_preference": [],
        "sync_library": {
            "steamid64": USER,
            "processed_games": 0,
            "total_games": 0,
            "library_last_synced_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata_enrichment": "complete",
        },
        "upsert_user_game_preference": saved,
        "delete_user_game_preference": None,
    }
    mocks = {}
    for name, value in values.items():
        mocks[name] = AsyncMock(return_value=value)
        monkeypatch.setattr(users, name, mocks[name])
    mocks["get_user_recommendations"] = AsyncMock(return_value=[])
    monkeypatch.setattr(
        recommendations, "get_user_recommendations", mocks["get_user_recommendations"]
    )
    return mocks


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, authenticated_user, services):
    async def override_db():
        yield fake_db

    coordinator = LibrarySyncCoordinator()

    async def override_coordinator():
        return coordinator

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    monkeypatch.setitem(
        app.dependency_overrides, users.get_sync_coordinator, override_coordinator
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"gamerec_session": SESSION},
    ) as client:
        yield client
    assert coordinator.pending == set()
    assert coordinator.retry_at == {}


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def assert_services_not_called(services):
    for service in services.values():
        service.assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("cookie", [None, "malformed", "x" * 43])
async def test_missing_malformed_or_unknown_session_cannot_access_accounts(
    client, services, operation, cookie
):
    client.cookies.clear()
    if cookie is not None:
        client.cookies.set("gamerec_session", cookie)
    response = await request(client, operation, headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_duplicate_session_cookies_fail_closed(client, services, operation):
    response = await request(
        client,
        operation,
        headers={
            "Origin": ORIGIN,
            "Cookie": f"gamerec_session={SESSION}; gamerec_session={'x' * 43}",
        },
    )
    assert response.status_code == 401
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_cross_account_access_is_denied_before_services(
    client, services, operation
):
    response = await request(
        client, operation, identity=OTHER, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "account_forbidden"
    assert USER not in response.text and OTHER not in response.text
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_disabled_auth_has_no_legacy_permission_fallback(
    client, services, monkeypatch, operation
):
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "steamid64_test", USER)
    response = await request(client, operation, headers={"Origin": ORIGIN})
    assert response.status_code == 503
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://untrusted.invalid"},
        {"Origin": "https://untrusted.invalid", "Referer": ORIGIN + "/library"},
        {"Referer": "https://localhost:5173.untrusted.invalid/"},
    ],
)
async def test_csrf_rejection_happens_before_any_mutating_service(
    client, services, operation, headers
):
    response = await request(client, operation, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_failed"
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_owner_access_uses_existing_services_and_private_responses(
    client, services, fake_db, operation
):
    response = await request(client, operation, headers={"Origin": ORIGIN})
    assert response.status_code == (204 if operation == "delete" else 200)
    assert_private(response)
    names = {
        "library": "get_user_library",
        "preferences": "get_user_game_preference",
        "recommendations": "get_user_recommendations",
        "sync": "sync_library",
        "put": "upsert_user_game_preference",
        "delete": "delete_user_game_preference",
    }
    services[names[operation]].assert_awaited_once()
    if operation == "recommendations":
        services[names[operation]].assert_awaited_once_with(fake_db, USER, limit=20)
    if operation == "library":
        assert response.json()["sync_allowed"] is True
        assert response.json()["preference_writes_allowed"] is True


@pytest.mark.parametrize("operation", WRITES)
async def test_trusted_referer_fallback_allows_owner_writes(client, operation):
    response = await request(
        client, operation, headers={"Referer": ORIGIN + "/library"}
    )
    assert response.status_code == (204 if operation == "delete" else 200)


@pytest.mark.parametrize("operation", ["library", "preferences"])
async def test_read_storage_failures_are_safe_503(client, services, operation):
    name = "get_user_library" if operation == "library" else "get_user_game_preference"
    services[name].side_effect = SQLAlchemyError(f"private SQL for {USER}")
    response = await request(client, operation)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert USER not in response.text and "private SQL" not in response.text
    assert_private(response)


@pytest.mark.parametrize("operation", ["library", "preferences"])
@pytest.mark.parametrize("identity", ["short", "７" * 17, USER + "%0A"])
async def test_invalid_read_id_is_422_without_service_call(
    client, services, operation, identity
):
    response = await request(client, operation, identity=identity)
    assert response.status_code == 422
    assert_private(response)
    assert_services_not_called(services)


@pytest.mark.parametrize("authenticated", [False, True])
async def test_session_and_ownership_rejection_precede_csrf(
    client, services, authenticated
):
    if not authenticated:
        client.cookies.clear()
    response = await request(client, "put", identity=OTHER)
    assert response.status_code == (403 if authenticated else 401)
    assert response.json()["detail"]["code"] == (
        "account_forbidden" if authenticated else "authentication_required"
    )
    assert_services_not_called(services)


async def test_unexpected_public_endpoint_error_keeps_public_header_policy(
    client, fake_db
):
    fake_db.execute.side_effect = TypeError("synthetic programming failure")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as public_client:
        response = await public_client.get("/games/")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "cache-control" not in response.headers
    assert "referrer-policy" not in response.headers
