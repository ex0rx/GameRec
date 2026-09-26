"""Recommendation retrieval and owned-game filtering without a Qdrant server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.services.user_recommendation import (
    filter_owned_games,
    get_user_recommendation_candidates,
)


@pytest.mark.asyncio
async def test_query_maps_qdrant_points_in_similarity_order():
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(id=20, payload={"name": "Second"}, score=0.9),
            SimpleNamespace(id=30, payload=None, score=0.7),
            SimpleNamespace(id=40, payload={}, score=0.4),
        ]
    )
    vector = [0.6, 0.8]

    result = await get_user_recommendation_candidates(client, vector, candidate_k=3)

    assert result == [
        {"steam_app_id": 20, "name": "Second", "score": 0.9},
        {"steam_app_id": 30, "name": None, "score": 0.7},
        {"steam_app_id": 40, "name": None, "score": 0.4},
    ]
    client.query_points.assert_awaited_once_with(
        collection_name=settings.qdrant_game_collection,
        query=vector,
        limit=3,
        with_payload=True,
        with_vectors=False,
    )
    client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_qdrant_response_returns_empty_candidates():
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(points=[])

    assert await get_user_recommendation_candidates(client, [1.0, 0.0]) == []
    assert client.query_points.await_args.kwargs["limit"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_k", [0, -1])
async def test_invalid_limit_never_queries_qdrant(candidate_k):
    client = AsyncMock(spec=AsyncQdrantClient)

    with pytest.raises(ValueError, match="candidate_k"):
        await get_user_recommendation_candidates(client, [1.0, 0.0], candidate_k)

    client.query_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_qdrant_error_propagates_without_closing_caller_client():
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.side_effect = RuntimeError("Qdrant unavailable")

    with pytest.raises(RuntimeError, match="Qdrant unavailable"):
        await get_user_recommendation_candidates(client, [1.0, 0.0])

    client.close.assert_not_awaited()


def test_filter_removes_every_owned_game_preserving_rank_and_inputs():
    candidates = [
        {"steam_app_id": 10, "score": 0.95},
        {"steam_app_id": 20, "score": 0.9},
        {"steam_app_id": 10, "score": 0.85},
        {"steam_app_id": 30, "score": 0.8},
    ]
    original = list(candidates)

    filtered = filter_owned_games(candidates, {10, 999})

    assert filtered == [candidates[1], candidates[3]]
    assert candidates == original
    assert filter_owned_games(candidates, set()) == candidates
    assert filter_owned_games([], {10}) == []
