"""FastAPI search request validation and service orchestration."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from qdrant_client.http.exceptions import ApiException

from gamerec.api import search, users
from gamerec.db import get_db
from gamerec.main import app
from gamerec.services.hybrid_search import HybridSearchResult, RankedSearchGame
from gamerec.services.query_embedding_client import EmbeddingServiceUnavailable
from gamerec.services.search_filters import SearchFilters

pytestmark = pytest.mark.asyncio


def search_result(*games):
    return HybridSearchResult([], len(games), frozenset(), frozenset(), list(games))


@pytest.fixture
def services(monkeypatch):
    embedding = AsyncMock(return_value=[0.0] * 384)
    ranking = AsyncMock(
        return_value=search_result(
            RankedSearchGame(108600, "Project Zomboid", 0.5, 0.8, 0.9, 0.605)
        )
    )
    qdrant = Mock(close=AsyncMock())
    monkeypatch.setattr(search, "get_query_embedding", embedding)
    monkeypatch.setattr(search, "hybrid_search_games", ranking)
    monkeypatch.setattr(search, "get_qdrant_client", lambda: qdrant)
    return embedding, ranking, qdrant


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, services):
    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_success_response_schema_and_trimmed_query(client, services, fake_db):
    embedding, ranking, qdrant = services
    response = await client.get(
        "/search/games", params={"query": "  survival crafting  "}
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "survival crafting",
        "total": 1,
        "limit": 20,
        "games": [
            {
                "steam_app_id": 108600,
                "name": "Project Zomboid",
                "similarity_score": 0.5,
                "popularity_score": 0.8,
                "review_quality": 0.9,
                "hybrid_score": 0.605,
            }
        ],
    }
    embedding.assert_awaited_once_with("survival crafting")
    ranking.assert_awaited_once_with(
        fake_db,
        qdrant,
        [0.0] * 384,
        top_k=20,
        filters=SearchFilters(),
    )
    qdrant.close.assert_awaited_once_with()


async def test_structured_filters_are_forwarded(client, services, fake_db):
    _, ranking, qdrant = services
    response = await client.get(
        "/search/games",
        params=[
            ("query", "survival crafting"),
            ("limit", "5"),
            ("genres", "Action"),
            ("genres", "RPG"),
            ("categories", "Co-op"),
            ("release_year_from", "2020"),
            ("release_year_to", "2025"),
            ("min_reviews", "500"),
        ],
    )
    assert response.status_code == 200
    ranking.assert_awaited_once_with(
        fake_db,
        qdrant,
        [0.0] * 384,
        top_k=5,
        filters=SearchFilters(
            genres=["Action", "RPG"],
            categories=["Co-op"],
            release_year_from=2020,
            release_year_to=2025,
            min_reviews=500,
        ),
    )


async def test_empty_results_are_successful(client, services):
    _, ranking, qdrant = services
    ranking.return_value = search_result()
    response = await client.get("/search/games?query=unmatched")
    assert response.status_code == 200
    assert response.json() == {
        "query": "unmatched",
        "total": 0,
        "limit": 20,
        "games": [],
    }
    qdrant.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "games", "limit": "0"},
        {"query": "games", "limit": "101"},
        {"query": "games", "genres": "  "},
        {"query": "games", "categories": ""},
        {"query": "games", "release_year_from": "0"},
        {"query": "games", "release_year_from": "2025", "release_year_to": "2020"},
        {"query": "games", "min_reviews": "-1"},
    ],
)
async def test_invalid_parameters_return_422_without_upstream_calls(
    client, services, params
):
    embedding, ranking, qdrant = services
    response = await client.get("/search/games", params=params)
    assert response.status_code == 422
    embedding.assert_not_awaited()
    ranking.assert_not_awaited()
    qdrant.close.assert_not_awaited()


async def test_embedding_failure_returns_503_without_details(client, services):
    embedding, ranking, qdrant = services
    embedding.side_effect = EmbeddingServiceUnavailable("private upstream details")
    response = await client.get("/search/games?query=survival")
    assert response.status_code == 503
    assert response.json() == {"detail": "Search temporarily unavailable"}
    assert "private upstream details" not in response.text
    ranking.assert_not_awaited()
    qdrant.close.assert_not_awaited()


@pytest.mark.parametrize("error", [ApiException("down"), httpx.ConnectError("down")])
async def test_search_failure_returns_503_and_closes_qdrant(client, services, error):
    _, ranking, qdrant = services
    ranking.side_effect = error
    response = await client.get("/search/games?query=survival")
    assert response.status_code == 503
    assert response.json() == {"detail": "Search temporarily unavailable"}
    qdrant.close.assert_awaited_once_with()


async def test_existing_user_route_still_works(client, monkeypatch):
    monkeypatch.setattr(users, "get_user_library", AsyncMock(return_value=None))
    response = await client.get("/users/synthetic-user/library")
    assert response.status_code == 404
