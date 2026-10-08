"""Semantic search maps Qdrant hits without touching PostgreSQL."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.services.semantic_search import search_games_by_embedding

pytestmark = pytest.mark.asyncio


def hit(app_id, name, score):
    return SimpleNamespace(id=app_id, payload=name, score=score)


@pytest.fixture
def client(compatible_collection_info):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.return_value = compatible_collection_info
    return client


async def test_query_arguments_mapping_and_order(client):
    embedding = [1.0] + [0.0] * 383
    client.query_points.return_value = SimpleNamespace(
        points=[
            hit(20, {"name": "First", "steam_app_id": 999}, 0.9),
            hit("30", {"name": "Second"}, 0.7),
            hit(10, {"name": "Third"}, 0.2),
        ]
    )

    results = await search_games_by_embedding(client, embedding, top_k=3)

    client.get_collection.assert_awaited_once_with(settings.qdrant_game_collection)
    client.query_points.assert_awaited_once_with(
        collection_name=settings.qdrant_game_collection,
        query=embedding,
        limit=3,
        with_payload=["name"],
        with_vectors=False,
    )
    assert results == [
        {"steam_app_id": 20, "name": "First", "score": 0.9},
        {"steam_app_id": 30, "name": "Second", "score": 0.7},
        {"steam_app_id": 10, "name": "Third", "score": 0.2},
    ]
    client.close.assert_not_awaited()


async def test_default_limit_and_empty_results(client):
    client.query_points.return_value = SimpleNamespace(points=[])
    assert await search_games_by_embedding(client, [0.0] * 384) == []
    assert client.query_points.await_args.kwargs["limit"] == 20


async def test_recall_diagnostic_limit_is_allowed(client):
    client.query_points.return_value = SimpleNamespace(points=[])
    assert await search_games_by_embedding(client, [0.0] * 384, top_k=1000) == []
    assert client.query_points.await_args.kwargs["limit"] == 1000


@pytest.mark.parametrize("top_k", [0, -1, 1001, True, 1.5, "10"])
async def test_invalid_limit_does_no_io(client, top_k):
    with pytest.raises(ValueError, match="top_k"):
        await search_games_by_embedding(client, [0.0] * 384, top_k)
    client.get_collection.assert_not_awaited()
    client.query_points.assert_not_awaited()


async def test_invalid_embedding_size_does_no_io(client):
    with pytest.raises(ValueError, match="Expected 384 dimensions"):
        await search_games_by_embedding(client, [1.0])
    client.get_collection.assert_not_awaited()
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("payload", [None, {}, {"name": None}, {"name": 42}])
async def test_missing_or_invalid_name_matches_existing_convention(client, payload):
    client.query_points.return_value = SimpleNamespace(points=[hit(20, payload, 0.5)])
    assert await search_games_by_embedding(client, [0.0] * 384) == [
        {"steam_app_id": 20, "name": "", "score": 0.5}
    ]


async def test_qdrant_error_propagates_without_closing_caller_client(client):
    client.query_points.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await search_games_by_embedding(client, [0.0] * 384)
    client.close.assert_not_awaited()
