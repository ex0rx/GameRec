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


async def test_missing_target_does_not_query(compatible_collection_info):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.return_value = compatible_collection_info
    client.retrieve.return_value = []
    assert await find_similar_games(client, 10) == []
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("vector", [None, [], {"named": [1.0]}, [[1.0]]])
async def test_invalid_target_vector_does_not_issue_unintended_query(
    vector, compatible_collection_info
):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.return_value = compatible_collection_info
    client.retrieve.return_value = [Record(id=10, vector=vector)]
    with pytest.raises(ValueError, match="unnamed dense"):
        await find_similar_games(client, 10)
    client.query_points.assert_not_awaited()


@pytest.mark.parametrize("operation", ["retrieve", "query_points"])
async def test_service_errors_propagate(operation, compatible_collection_info):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.get_collection.return_value = compatible_collection_info
    client.retrieve.return_value = [Record(id=10, vector=[1.0] + [0.0] * 383)]
    getattr(client, operation).side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await find_similar_games(client, 10)
    client.close.assert_not_awaited()


@pytest_asyncio.fixture
async def metadata_qdrant(qdrant):
    # Scores descend in ID order; the target also matches all supplied filters.
    payloads = [
        {"genres": ["Action", "RPG"], "categories": ["Co-op", "Multi-player"]},
        {"genres": ["Action"], "categories": ["Single-player"]},
        {"genres": ["RPG"], "categories": ["Co-op"]},
        {"genres": ["Action", "RPG"], "categories": ["Co-op"]},
        {"genres": ["Action", "RPG"], "categories": ["Co-op", "Multi-player"]},
        {"genres": None, "categories": None},
        {},
        {"genres": [], "categories": []},
    ]
    await upsert_game_points(
        qdrant,
        [
            point(i, 1.0, float(i), {"name": f"Game {i}", **payload})
            for i, payload in enumerate(payloads)
        ],
    )
    return qdrant


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({}, [1, 2, 3, 4, 5, 6, 7]),
        ({"genres": [], "categories": []}, [1, 2, 3, 4, 5, 6, 7]),
        ({"genres": None, "categories": None}, [1, 2, 3, 4, 5, 6, 7]),
        ({"genres": ["Action"]}, [1, 3, 4]),
        ({"categories": ["Co-op"]}, [2, 3, 4]),
        ({"genres": ["Action"], "categories": ["Co-op"]}, [3, 4]),
        ({"genres": ["Action", "RPG"]}, [3, 4]),
        ({"categories": ["Co-op", "Multi-player"]}, [4]),
        ({"genres": ["Action", "RPG"], "categories": ["Co-op", "Multi-player"]}, [4]),
        ({"genres": ["Action", "Action"]}, [1, 3, 4]),
        ({"genres": ["Missing"]}, []),
        ({"categories": ["Missing"]}, []),
        ({"genres": ["Action"], "categories": ["Missing"]}, []),
        ({"genres": ["action"]}, []),
        ({"genres": [], "categories": ["Co-op"]}, [2, 3, 4]),
    ],
)
async def test_metadata_filters_and_semantics(metadata_qdrant, filters, expected):
    results = await find_similar_games(metadata_qdrant, 0, top_k=20, **filters)
    assert [r["steam_app_id"] for r in results] == expected
    assert [r["name"] for r in results] == [f"Game {i}" for i in expected]
    assert [r["score"] for r in results] == sorted(
        [r["score"] for r in results], reverse=True
    )


async def test_filters_apply_before_limit_and_only_to_candidates(metadata_qdrant):
    # Target 1 lacks Co-op; it can still query candidates that have it.
    results = await find_similar_games(
        metadata_qdrant, 1, top_k=1, genres=["RPG"], categories=["Co-op"]
    )
    assert [r["steam_app_id"] for r in results] == [2]
    assert await find_similar_games(metadata_qdrant, 999, genres=["RPG"]) == []


async def test_search_never_creates_indexes(metadata_qdrant, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(metadata_qdrant, "create_payload_index", create)
    await find_similar_games(metadata_qdrant, 0, genres=["Action"])
    create.assert_not_awaited()
