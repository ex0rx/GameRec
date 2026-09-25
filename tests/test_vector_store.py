"""Retrieval against Qdrant's isolated local engine and mocked I/O boundaries."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, HasIdCondition, PointStruct, Record

from gamerec.core.config import settings
from gamerec.services.vector_store import (
    ensure_game_collection,
    find_similar_games,
    upsert_game_points,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def qdrant(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_game_collection", "isolated-retrieval")
    client = AsyncQdrantClient(":memory:")
    try:
        await ensure_game_collection(client)
        yield client
    finally:
        await client.close()


def point(appid, x, y, payload=None):
    return PointStruct(id=appid, vector=[x, y] + [0.0] * 382, payload=payload)


@pytest.mark.parametrize("top_k", [1, 2, 3, 20])
async def test_order_self_exclusion_limits_and_mapping(qdrant, top_k):
    await upsert_game_points(
        qdrant,
        [
            point(70, -1.0, 0.0, {"name": "Opposite"}),
            point(90, 1.0, 0.0, {"name": "Target"}),
            point(10, 0.0, 1.0, {"name": "Orthogonal"}),
            point(55, 3.0, 4.0, {"name": "Near", "steam_app_id": 999}),
        ],
    )
    results = await find_similar_games(qdrant, 90, top_k)
    expected = [(55, "Near", 0.6), (10, "Orthogonal", 0.0), (70, "Opposite", -1.0)][
        :top_k
    ]
    assert [(r["steam_app_id"], r["name"]) for r in results] == [
        (appid, name) for appid, name, _ in expected
    ]
    assert [r["score"] for r in results] == pytest.approx([s for _, _, s in expected])


async def test_identical_vectors_do_not_exclude_other_games(qdrant):
    await upsert_game_points(qdrant, [point(i, 1.0, 0.0) for i in [10, 20, 30]])
    results = await find_similar_games(qdrant, 20, 2)
    assert {r["steam_app_id"] for r in results} == {10, 30}
    assert [r["score"] for r in results] == pytest.approx([1.0, 1.0])


async def test_empty_missing_and_target_without_neighbours(qdrant):
    assert await find_similar_games(qdrant, 10) == []
    await upsert_game_points(qdrant, [point(10, 1.0, 0.0)])
    assert await find_similar_games(qdrant, 999) == []
    assert await find_similar_games(qdrant, 10) == []


@pytest.mark.parametrize("payload", [None, {}, {"name": None}, {"name": 42}])
async def test_missing_or_invalid_name(qdrant, payload):
    await upsert_game_points(
        qdrant, [point(10, 1.0, 0.0), point(20, 3.0, 4.0, payload)]
    )
    (result,) = await find_similar_games(qdrant, 10)
    assert result["steam_app_id"] == 20
    assert result["name"] == ""


@pytest.mark.parametrize("top_k", [0, -1])
async def test_nonpositive_limit_does_no_io(top_k):
    client = AsyncMock(spec=AsyncQdrantClient)
    assert await find_similar_games(client, 10, top_k) == []
    client.retrieve.assert_not_awaited()
    client.query_points.assert_not_awaited()


async def test_query_uses_retrieved_vector_and_id_exclusion(qdrant, monkeypatch):
    await upsert_game_points(qdrant, [point(10, 1.0, 0.0)])
    retrieve = AsyncMock(wraps=qdrant.retrieve)
    query = AsyncMock(wraps=qdrant.query_points)
    monkeypatch.setattr(qdrant, "retrieve", retrieve)
    monkeypatch.setattr(qdrant, "query_points", query)
    assert await find_similar_games(qdrant, 10, 3) == []
    retrieve.assert_awaited_once_with(
        collection_name=settings.qdrant_game_collection,
        ids=[10],
        with_vectors=True,
        with_payload=False,
    )
    query.assert_awaited_once_with(
        collection_name=settings.qdrant_game_collection,
        query=[1.0] + [0.0] * 383,
        query_filter=Filter(must_not=[HasIdCondition(has_id=[10])]),
        limit=3,
        with_payload=["name"],
        with_vectors=False,
    )


async def test_missing_target_does_not_query():
    client = AsyncMock(spec=AsyncQdrantClient)
    client.retrieve.return_value = []
    assert await find_similar_games(client, 10) == []
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("vector", [None, [], {"named": [1.0]}, [[1.0]]])
async def test_invalid_target_vector_does_not_issue_unintended_query(vector):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.retrieve.return_value = [Record(id=10, vector=vector)]
    with pytest.raises(ValueError, match="unnamed dense"):
        await find_similar_games(client, 10)
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("operation", ["retrieve", "query_points"])
async def test_service_errors_propagate(operation):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.retrieve.return_value = [Record(id=10, vector=[1.0] + [0.0] * 383)]
    getattr(client, operation).side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await find_similar_games(client, 10)
    client.close.assert_not_awaited()
