"""On-demand API orchestration with real explanation services and mocked I/O."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import SQLAlchemyError

from gamerec.api import explanations, search
from gamerec.db import get_db
from gamerec.main import app
from gamerec.schemas.explanation import SearchExplanation
from gamerec.services.hybrid_search import HybridSearchResult

pytestmark = pytest.mark.asyncio

REQUEST = {"steam_app_id": 108600, "search_query": "Cooperative survival crafting"}
OUTPUT = {
    "explanation": "Cooperative survival and crafting offer a shared challenge.",
    "matching_features": ["Co-op", "Crafting"],
}


@pytest.fixture
def game(fake_db):
    game = SimpleNamespace(
        steam_app_id=108600,
        name="Project Zomboid",
        short_description="Loot, build and craft to survive together.",
        genres=["RPG"],
        categories=["Co-op"],
        release_date=None,
    )
    fake_db.execute.return_value = SimpleNamespace(one_or_none=Mock(return_value=game))
    return game


@pytest.fixture
def model(monkeypatch):
    state = SimpleNamespace(clients=[], requests=[], error=None, content=OUTPUT)

    def handler(request):
        state.requests.append(request)
        if isinstance(state.error, httpx.RequestError):
            raise state.error
        if state.error == "missing_model":
            return httpx.Response(404, json={"error": "model 'private' not found"})
        if state.error == "server":
            return httpx.Response(500, text="private server failure")
        if state.error == "envelope":
            return httpx.Response(200, text="private invalid JSON")
        return httpx.Response(
            200,
            json={
                "model": "configured-model",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(state.content),
                },
                "done": True,
                "done_reason": "stop",
            },
        )

    def factory():
        client = httpx.AsyncClient(
            base_url="http://model-server:11434",
            transport=httpx.MockTransport(handler),
        )
        state.clients.append(client)
        return client

    monkeypatch.setattr(explanations, "create_ollama_client", factory)
    return state


@pytest_asyncio.fixture
async def client(monkeypatch, fake_db, game, model):
    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_success_reuses_context_and_model_schema_and_closes_client(
    client, fake_db, model
):
    response = await client.post(
        "/explanations/search",
        json={**REQUEST, "search_query": "  Cooperative survival crafting  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "steam_app_id": 108600,
        "game_name": "Project Zomboid",
        **OUTPUT,
    }
    fake_db.execute.assert_awaited_once()
    statement = fake_db.execute.call_args.args[0]
    assert statement.is_select
    assert list(statement.compile().params.values()) == [108600]
    assert statement.get_execution_options()["autoflush"] is False
    assert len(model.requests) == 1
    request = model.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://model-server:11434/api/chat"
    body = json.loads(request.content)
    assert body["format"] == SearchExplanation.model_json_schema()
    data = json.loads(body["messages"][1]["content"])
    assert data["search_query"] == REQUEST["search_query"]
    assert "Loot, build and craft" in data["game_context"]
    assert len(model.clients) == 1
    assert model.clients[0].is_closed


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"search_query": "survival"},
        {"steam_app_id": 108600},
        {**REQUEST, "search_query": " \t\n"},
        {**REQUEST, "search_query": "x" * 501},
        {**REQUEST, "search_query": None},
        {**REQUEST, "search_query": 123},
        {**REQUEST, "search_query": True},
        {**REQUEST, "search_query": ["survival"]},
        {**REQUEST, "steam_app_id": 0},
        {**REQUEST, "steam_app_id": -1},
        {**REQUEST, "steam_app_id": None},
        {**REQUEST, "steam_app_id": "108600"},
        {**REQUEST, "steam_app_id": 108600.0},
        {**REQUEST, "steam_app_id": True},
    ],
)
async def test_invalid_bodies_return_422_without_work(client, fake_db, model, body):
    response = await client.post("/explanations/search", json=body)
    assert response.status_code == 422
    fake_db.execute.assert_not_awaited()
    assert model.clients == []
    assert model.requests == []


async def test_accepts_unicode_and_maximum_length_after_trimming(client, model):
    query = "探索 " + "x" * 497
    response = await client.post(
        "/explanations/search", json={**REQUEST, "search_query": f"  {query}  "}
    )
    assert response.status_code == 200
    data = json.loads(json.loads(model.requests[0].content)["messages"][1]["content"])
    assert data["search_query"] == query


async def test_missing_game_returns_404_without_model_call(client, fake_db, model):
    fake_db.execute.return_value.one_or_none.return_value = None
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 404
    assert response.json() == {"detail": "Game not found"}
    assert model.clients == []
    assert model.requests == []


async def test_insufficient_metadata_returns_existing_fallback_without_inference(
    client, game, model
):
    game.short_description = None
    game.genres = []
    game.categories = []
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 200
    assert response.json() == {
        "steam_app_id": 108600,
        "game_name": "Project Zomboid",
        "explanation": "Not enough stored game information is available to explain why this game might interest you.",
        "matching_features": [],
    }
    assert model.requests == []
    assert model.clients[0].is_closed


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (
            httpx.ConnectError("private connection details"),
            503,
            "Explanation temporarily unavailable",
        ),
        ("missing_model", 503, "Explanation temporarily unavailable"),
        ("server", 503, "Explanation temporarily unavailable"),
        (
            httpx.ReadTimeout("private timeout details"),
            504,
            "Explanation generation timed out",
        ),
        (
            httpx.ConnectTimeout("private connect timeout details"),
            504,
            "Explanation generation timed out",
        ),
        ("envelope", 502, "Invalid response from explanation service"),
    ],
)
async def test_model_failure_mapping_and_cleanup(client, model, error, status, detail):
    model.error = error
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert "private" not in response.text
    assert len(model.requests) == 1  # No retries or validation generation.
    assert model.clients[0].is_closed


@pytest.mark.parametrize(
    "output",
    [
        "not an explanation object",
        {"explanation": "private invalid content"},
        {"explanation": " ", "matching_features": []},
        {"explanation": "Text", "matching_features": [123]},
        {**OUTPUT, "raw_metadata": "private content"},
    ],
)
async def test_invalid_structured_output_returns_502(client, model, output):
    model.content = output
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 502
    assert response.json() == {"detail": "Invalid response from explanation service"}
    assert len(model.requests) == 1
    assert model.clients[0].is_closed


async def test_database_failure_returns_safe_503(client, fake_db, model):
    fake_db.execute.side_effect = SQLAlchemyError("private connection details")
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 503
    assert response.json() == {"detail": "Explanation temporarily unavailable"}
    assert model.clients == []


async def test_programming_error_remains_generic_500_and_closes_client(
    client, model, monkeypatch
):
    generate = AsyncMock(side_effect=TypeError("private programming failure"))
    monkeypatch.setattr(explanations, "generate_search_explanation", generate)
    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert model.clients[0].is_closed


async def test_explanation_is_read_only_and_does_not_retrieve_or_ingest(
    client, fake_db, model, monkeypatch
):
    from gamerec.integrations import qdrant, steam

    qdrant_factory = Mock(side_effect=AssertionError("Qdrant must not be used"))
    steam_fetch = AsyncMock(side_effect=AssertionError("Steam must not be used"))
    monkeypatch.setattr(qdrant, "get_qdrant_client", qdrant_factory)
    monkeypatch.setattr(search, "get_qdrant_client", qdrant_factory)
    monkeypatch.setattr(steam, "fetch_steam_app_details", steam_fetch)

    response = await client.post("/explanations/search", json=REQUEST)
    assert response.status_code == 200
    qdrant_factory.assert_not_called()
    steam_fetch.assert_not_awaited()
    fake_db.commit.assert_not_awaited()
    fake_db.flush.assert_not_called()
    fake_db.add.assert_not_called()
    fake_db.delete.assert_not_called()
    assert all(call.args[0].is_select for call in fake_db.execute.call_args_list)
    assert len(model.requests) == 1


async def test_search_does_not_generate_explanations(client, monkeypatch, model):
    generate = AsyncMock(side_effect=AssertionError("Explanations are on demand"))
    monkeypatch.setattr(explanations, "generate_search_explanation", generate)
    monkeypatch.setattr(
        search, "get_query_embedding", AsyncMock(return_value=[0.0] * 384)
    )
    monkeypatch.setattr(
        search,
        "hybrid_search_games",
        AsyncMock(return_value=HybridSearchResult([], 0, frozenset(), frozenset(), [])),
    )
    qdrant = Mock(close=AsyncMock())
    monkeypatch.setattr(search, "get_qdrant_client", lambda: qdrant)

    response = await client.get("/search/games?query=survival")
    assert response.status_code == 200
    assert response.json() == {
        "query": "survival",
        "total": 0,
        "limit": 20,
        "games": [],
    }
    generate.assert_not_awaited()
    assert model.clients == []
    qdrant.close.assert_awaited_once_with()


async def test_search_remains_available_after_explanation_failure(
    client, monkeypatch, model
):
    model.error = httpx.ConnectError("private model failure")
    failed = await client.post("/explanations/search", json=REQUEST)
    assert failed.status_code == 503
    monkeypatch.setattr(
        search, "get_query_embedding", AsyncMock(return_value=[0.0] * 384)
    )
    monkeypatch.setattr(
        search,
        "hybrid_search_games",
        AsyncMock(return_value=HybridSearchResult([], 0, frozenset(), frozenset(), [])),
    )
    qdrant = Mock(close=AsyncMock())
    monkeypatch.setattr(search, "get_qdrant_client", lambda: qdrant)
    response = await client.get("/search/games?query=survival")
    assert response.status_code == 200
    assert len(model.requests) == 1
    assert model.clients[0].is_closed
    qdrant.close.assert_awaited_once_with()


async def test_recommendation_retrieval_and_ranking_do_not_use_ollama(
    monkeypatch, fake_db
):
    from gamerec.integrations import ollama
    from gamerec.services import search_explanation, user_recommendation
    from gamerec.services.hybrid_user_recommendation import rank_candidates

    forbidden = Mock(side_effect=AssertionError("Recommendations must not use Ollama"))
    monkeypatch.setattr(ollama, "create_ollama_client", forbidden)
    monkeypatch.setattr(ollama, "chat_with_ollama", forbidden)
    monkeypatch.setattr(search_explanation, "chat_with_ollama", forbidden)
    monkeypatch.setattr(search_explanation, "generate_search_explanation", forbidden)
    monkeypatch.setattr(
        user_recommendation, "get_representative_seed_games", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        user_recommendation,
        "fetch_game_vectors",
        AsyncMock(return_value={10: [1.0, 0.0]}),
    )
    qdrant = SimpleNamespace(
        query_points=AsyncMock(
            return_value=SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id=10, score=0.8, payload={"name": "Synthetic game"}
                    )
                ]
            )
        )
    )
    result = await user_recommendation.get_multi_source_recommendation_candidates(
        fake_db, qdrant, "synthetic-user", [1.0, 0.0], set()
    )
    ranked = rank_candidates(result.candidates, {}, {})
    assert [candidate.steam_app_id for candidate in ranked] == [10]
    forbidden.assert_not_called()
