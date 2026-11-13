"""Recommendation HTTP contracts; all data/storage services are mocked."""

from dataclasses import asdict
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from qdrant_client.http.exceptions import UnexpectedResponse
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import recommendations
from gamerec.db import get_db
from gamerec.main import app
from gamerec.services.hybrid_user_recommendation import RankedCandidate
from gamerec.services.user_game_preferences import UserNotFoundError

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"  # Synthetic, deliberately beyond JS safe integers.
GAME = RankedCandidate(20, None, 0.9, 0.8, 0.9, 0.7, 0.4, 0.6)


@pytest.fixture
def service(monkeypatch):
    service = AsyncMock(return_value=[GAME])
    monkeypatch.setattr(recommendations, "get_user_recommendations", service)
    return service


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, service, authenticated_user):
    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        cookies={"gamerec_session": "s" * 43},
    ) as client:
        yield client


async def test_default_contract_keeps_string_id_out_of_response(
    client, service, fake_db
):
    response = await client.post("/recommendations", json={"steamid64": USER})
    assert response.status_code == 200
    assert response.json() == {"total": 1, "limit": 20, "games": [asdict(GAME)]}
    assert USER not in response.text
    service.assert_awaited_once_with(fake_db, USER, limit=20)


async def test_explicit_limit_and_order_are_forwarded(client, service, fake_db):
    second = RankedCandidate(30, "Other game", 0.7, 0.5, 0.9, 0.6, 0.3, 0.1)
    service.return_value = [GAME, second]
    response = await client.post(
        "/recommendations", json={"steamid64": USER, "limit": 2}
    )
    assert response.json() == {
        "total": 2,
        "limit": 2,
        "games": [asdict(GAME), asdict(second)],
    }
    service.assert_awaited_once_with(fake_db, USER, limit=2)


async def test_empty_recommendations_do_not_claim_empty_library(client, service):
    service.return_value = []
    response = await client.post("/recommendations", json={"steamid64": USER})
    assert response.status_code == 200
    assert response.json() == {"total": 0, "limit": 20, "games": []}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"steamid64": None},
        {"steamid64": 76561190000000001},
        {"steamid64": ""},
        {"steamid64": " "},
        {"steamid64": "not-an-id"},
        {"steamid64": "-1"},
        {"steamid64": "0"},
        {"steamid64": "001"},
        {"steamid64": str(2**64)},
        {"steamid64": "1" * 21},
        {"steamid64": USER, "limit": 0},
        {"steamid64": USER, "limit": 101},
        {"steamid64": USER, "limit": True},
        {"steamid64": USER, "limit": "20"},
        {"steamid64": USER, "limit": 1.5},
    ],
)
async def test_invalid_body_returns_422_without_recommending(client, service, payload):
    assert (await client.post("/recommendations", json=payload)).status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize("steamid64", ["1", str(2**64 - 1)])
async def test_valid_unsigned_boundaries_cannot_select_another_account(
    client, service, steamid64
):
    response = await client.post("/recommendations", json={"steamid64": steamid64})
    assert response.status_code == 403
    service.assert_not_called()


async def test_unknown_user_has_safe_404(client, service):
    service.side_effect = UserNotFoundError()
    response = await client.post("/recommendations", json={"steamid64": USER})
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


@pytest.mark.parametrize(
    "error",
    [
        SQLAlchemyError(f"private SQL parameter: {USER}"),
        httpx.ConnectError(f"private parameter: {USER}"),
        OSError(f"private parameter: {USER}"),
        UnexpectedResponse(
            503, "unavailable", b"private storage response", httpx.Headers()
        ),
    ],
)
async def test_dependency_errors_do_not_leak_parameters(client, service, caplog, error):
    service.side_effect = error
    response = await client.post("/recommendations", json={"steamid64": USER})
    assert response.status_code == 503
    assert response.json() == {"detail": "Recommendations temporarily unavailable"}
    assert USER not in response.text + caplog.text
    assert "private" not in caplog.text


async def test_programming_errors_are_not_hidden_as_dependency_failures(
    client, service
):
    service.side_effect = TypeError("synthetic programming failure")
    response = await client.post("/recommendations", json={"steamid64": USER})
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_post_body_is_required_not_a_user_id_url(client, service):
    assert (await client.get("/recommendations")).status_code == 405
    assert (await client.post(f"/recommendations/{USER}")).status_code == 404
    service.assert_not_called()
