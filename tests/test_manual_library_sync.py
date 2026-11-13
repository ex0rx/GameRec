"""Bounded manual sync contracts; all Steam HTTP and persistence are isolated."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import users
from gamerec.core.config import settings
from gamerec.db import get_db
from gamerec.main import app
from gamerec.services import manual_library_sync as sync

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"
OTHER = "76561190000000002"
SAVED = datetime(2026, 1, 2, tzinfo=UTC)
HTTP_CLIENT = httpx.AsyncClient


def payload(count=1):
    return {
        "response": {
            "game_count": count,
            "games": [
                {"appid": i + 10, "name": f"Game {i}", "playtime_forever": 60}
                for i in range(count)
            ],
        }
    }


@pytest.fixture
def pipeline(monkeypatch, fake_db):
    clients = []

    def client(**kwargs):
        instance = HTTP_CLIENT(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)), **kwargs
        )
        clients.append(instance)
        return instance

    monkeypatch.setattr(sync.httpx, "AsyncClient", client)
    fetch = AsyncMock(return_value=payload())
    save = AsyncMock(return_value=1)
    metadata = AsyncMock(return_value=1)
    monkeypatch.setattr(sync, "fetch_steam_owned_games", fetch)
    monkeypatch.setattr(sync, "save_steam_library", save)
    monkeypatch.setattr(sync, "get_steam_metadata", metadata)
    fake_db.scalar = AsyncMock(side_effect=[None, SAVED, 1, 0])
    fake_db.rollback = AsyncMock()
    return fetch, save, metadata, clients


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, authenticated_user):
    coordinator = sync.LibrarySyncCoordinator()

    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)

    async def override_coordinator():
        return coordinator

    monkeypatch.setitem(
        app.dependency_overrides, users.get_sync_coordinator, override_coordinator
    )
    async with HTTP_CLIENT(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"gamerec_session": "s" * 43},
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        yield client


@pytest.mark.parametrize(
    "identity,cookie,status",
    [(USER, None, 401), (USER, "bad", 401), (OTHER, "s" * 43, 403)],
)
async def test_authorization_fails_before_storage_or_steam(
    client, fake_db, pipeline, identity, cookie, status
):
    client.cookies.clear()
    if cookie is not None:
        client.cookies.set("gamerec_session", cookie)
    response = await client.post(f"/users/{identity}/library/sync")
    assert response.status_code == status
    pipeline[0].assert_not_called()
    fake_db.scalar.assert_not_called()


@pytest.mark.parametrize(
    "identity", ["123", USER + "0", "a" * 17, "７" * 17, "-" + USER[1:]]
)
async def test_invalid_id_is_422(client, pipeline, identity):
    response = await client.post(f"/users/{identity}/library/sync")
    assert response.status_code == 422
    pipeline[0].assert_not_called()


async def test_completed_unknown_user_and_bounded_metadata(client, fake_db, pipeline):
    fetch, save, metadata, clients = pipeline
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 200
    assert response.json() == {
        "steamid64": USER,
        "status": "completed",
        "processed_games": 1,
        "total_games": 1,
        "library_last_synced_at": "2026-01-02T00:00:00Z",
        "metadata_enrichment": "complete",
    }
    fetch.assert_awaited_once_with(
        client=clients[0],
        steamid64=USER,
        include_appinfo=True,
        include_played_free_games=True,
    )
    assert save.await_args.args[1] == USER
    assert save.await_args.args[2][0]["steam_app_id"] == 10
    metadata.assert_awaited_once_with(
        db=fake_db,
        client=clients[0],
        batch_size=10,
        max_games=10,
        request_delay=0.25,
        max_concurrent_requests=2,
        max_consecutive_rate_limits=3,
        steam_app_ids=[10],
    )
    assert clients[0].is_closed


@pytest.mark.parametrize(
    "owned,status,code",
    [
        ({"response": {}}, 503, "steam_unavailable"),
        ({"response": {"game_count": 1, "games": []}}, 502, "steam_upstream_error"),
    ],
)
async def test_unavailable_or_malformed_never_saved(
    client, pipeline, owned, status, code
):
    pipeline[0].return_value = owned
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    pipeline[1].assert_not_called()
    pipeline[2].assert_not_called()
    assert pipeline[3][0].is_closed


@pytest.mark.parametrize(
    "error,status,code",
    [
        (httpx.ConnectError("secret URL"), 502, "steam_upstream_error"),
        (httpx.ReadTimeout("secret URL"), 504, "sync_timeout"),
        (SQLAlchemyError("secret SQL"), 503, "database_unavailable"),
    ],
)
async def test_safe_failures(client, pipeline, error, status, code):
    pipeline[0].side_effect = error
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "secret" not in response.text
    pipeline[1].assert_not_called()
    assert pipeline[3][0].is_closed


@pytest.mark.parametrize(
    "metadata_failure",
    [TimeoutError(), httpx.ConnectError("offline"), SQLAlchemyError("offline")],
)
async def test_metadata_failure_reports_committed_partial(
    client, fake_db, pipeline, metadata_failure
):
    pipeline[2].side_effect = metadata_failure
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 200
    assert response.json()["metadata_enrichment"] == "partial"
    assert response.json()["processed_games"] == 1
    pipeline[1].assert_awaited_once()
    assert fake_db.rollback.await_count >= 3
    assert pipeline[3][0].is_closed


async def test_unenriched_candidates_are_partial(client, fake_db, pipeline):
    fake_db.scalar.side_effect = [None, SAVED, 11, 1]
    pipeline[0].return_value = payload(11)
    pipeline[1].return_value = 11
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.json()["metadata_enrichment"] == "partial"
    assert pipeline[2].await_args.kwargs["max_games"] == 10
    assert len(pipeline[2].await_args.kwargs["steam_app_ids"]) == 11


async def test_running_request_rejected_and_cancellation_releases(fake_db, pipeline):
    started = asyncio.Event()
    hold = asyncio.Event()

    async def fetch(**kwargs):
        started.set()
        await hold.wait()

    pipeline[0].side_effect = fetch
    coordinator = sync.LibrarySyncCoordinator()
    task = asyncio.create_task(sync.sync_library(fake_db, USER, coordinator))
    await started.wait()
    with pytest.raises(sync.LibrarySyncError) as conflict:
        await sync.sync_library(fake_db, USER, coordinator)
    assert conflict.value.status == 409
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert USER not in coordinator.pending
    assert pipeline[3][0].is_closed
    pipeline[1].assert_not_called()
    with pytest.raises(sync.LibrarySyncError) as cooldown:
        coordinator.begin_attempt(USER, None)
    assert cooldown.value.status == 429


async def test_core_budget_releases_and_keeps_attempt_cooldown(
    monkeypatch, fake_db, pipeline
):
    monkeypatch.setattr(sync, "CORE_TIMEOUT_SECONDS", 0.01)

    async def stalled(**kwargs):
        await asyncio.Event().wait()

    pipeline[0].side_effect = stalled
    coordinator = sync.LibrarySyncCoordinator()
    with pytest.raises(TimeoutError):
        await sync.sync_library(fake_db, USER, coordinator)
    assert coordinator.pending == set()
    assert pipeline[3][0].is_closed
    pipeline[1].assert_not_called()
    with pytest.raises(sync.LibrarySyncError) as error:
        coordinator.begin_attempt(USER, None)
    assert error.value.retry_after >= 59


async def test_metadata_budget_returns_partial(monkeypatch, client, pipeline):
    monkeypatch.setattr(sync, "METADATA_TIMEOUT_SECONDS", 0.01)

    async def stalled(**kwargs):
        await asyncio.Event().wait()

    pipeline[2].side_effect = stalled
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 200
    assert response.json()["metadata_enrichment"] == "partial"
    assert pipeline[3][0].is_closed


async def test_failed_attempt_cooldown_header_does_not_extend(client, pipeline):
    pipeline[0].return_value = {"response": {}}
    assert (await client.post(f"/users/{USER}/library/sync")).status_code == 503
    # Each rejected attempt still reads persisted sync time, but never Steam.
    pipeline[0].reset_mock()
    # Use the same snapshot for every cooldown preflight.
    db_generator = app.dependency_overrides[get_db]()
    db = await anext(db_generator)
    db.scalar.side_effect = None
    db.scalar.return_value = None
    responses = [await client.post(f"/users/{USER}/library/sync") for _ in range(2)]
    assert [r.status_code for r in responses] == [429, 429]
    assert all(1 <= int(r.headers["Retry-After"]) <= 60 for r in responses)
    pipeline[0].assert_not_called()
    await db_generator.aclose()


async def test_recent_persisted_save_respected(client, fake_db, pipeline):
    fake_db.scalar.side_effect = None
    fake_db.scalar.return_value = datetime.now(UTC) - timedelta(seconds=10)
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 429
    assert 49 <= int(response.headers["Retry-After"]) <= 50
    pipeline[0].assert_not_called()


async def test_upstream_retry_delay_survives_timeout(monkeypatch, fake_db, pipeline):
    # Invalid delays must not poison future cooldowns with an infinite deadline.
    for invalid in (None, "not-a-date", "inf", "NaN", "-1"):
        assert sync.upstream_retry_seconds(invalid) == 0
    coordinator = sync.LibrarySyncCoordinator()

    async def throttled(**kwargs):
        response = httpx.Response(429, headers={"Retry-After": "180"})
        for hook in kwargs["client"].event_hooks["response"]:
            await hook(response)
        raise TimeoutError

    pipeline[0].side_effect = throttled
    with pytest.raises(TimeoutError):
        await sync.sync_library(fake_db, USER, coordinator)
    with pytest.raises(sync.LibrarySyncError) as error:
        coordinator.begin_attempt(USER, None)
    assert 179 <= error.value.retry_after <= 180
    assert coordinator.pending == set()


async def test_coordinator_rejections_do_not_extend_clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(sync.time, "monotonic", lambda: now[0])
    coordinator = sync.LibrarySyncCoordinator(60)
    coordinator.begin_attempt(USER, None)
    now[0] = 110
    with pytest.raises(sync.LibrarySyncError) as error:
        coordinator.begin_attempt(USER, None)
    assert error.value.retry_after == 50
    assert coordinator.retry_at[USER] == 160
    now[0] = 160
    coordinator.begin_attempt(USER, None)
    assert coordinator.retry_at[USER] == 220


async def test_library_capability_uses_owner_policy_in_all_environments(
    monkeypatch, client
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
    assert (await client.get(f"/users/{USER}/library")).json()["sync_allowed"] is True
    monkeypatch.setattr(settings, "environment", "production")
    assert (await client.get(f"/users/{USER}/library")).json()["sync_allowed"] is True


async def test_metadata_rollback_failure_retains_completed_import(
    client, fake_db, pipeline
):
    pipeline[2].side_effect = SQLAlchemyError("metadata unavailable")
    fake_db.rollback.side_effect = [None, None, SQLAlchemyError("rollback unavailable")]
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 200
    assert response.json()["metadata_enrichment"] == "partial"
    assert response.json()["total_games"] == 1
    assert pipeline[3][0].is_closed


async def test_lifespan_initialises_and_clears_coordinator(monkeypatch):
    monkeypatch.setattr(settings, "explanation_cache_enabled", False)
    monkeypatch.setattr(settings, "manual_library_sync_cooldown_seconds", 73)
    async with app.router.lifespan_context(app):
        coordinator = app.state.library_sync_coordinator
        assert isinstance(coordinator, sync.LibrarySyncCoordinator)
        assert coordinator.cooldown_seconds == 73
        coordinator.begin_attempt(USER, None)
    assert app.state.library_sync_coordinator is None
    async with app.router.lifespan_context(app):
        assert app.state.library_sync_coordinator is not coordinator
        assert app.state.library_sync_coordinator.retry_at == {}


async def test_post_encoded_trailing_newline_is_rejected(client, pipeline):
    response = await client.post(f"/users/{USER}%0A/library/sync")
    assert response.status_code == 422
    pipeline[0].assert_not_called()


@pytest.mark.parametrize("field", ["appid", "playtime_forever", "playtime_2weeks"])
async def test_unstorable_steam_integer_rejected_before_saving(client, pipeline, field):
    owned = payload()
    owned["response"]["games"][0][field] = 2**40
    pipeline[0].return_value = owned
    response = await client.post(f"/users/{USER}/library/sync")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "steam_upstream_error"
    pipeline[1].assert_not_called()
    pipeline[2].assert_not_called()
