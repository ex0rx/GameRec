"""Multi-vector retrieval with synthetic library rows and Qdrant responses."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.services import user_recommendation
from gamerec.services.hybrid_user_recommendation import rank_candidates
from gamerec.services.user_profile import UserGameProfile


def game(appid, vector, minutes=0, preference=None):
    return UserGameProfile(appid, vector, minutes, preference)


def point(appid, score, name=None):
    return SimpleNamespace(id=appid, score=score, payload={"name": name})


def qdrant_client(responses):
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.side_effect = [
        SimpleNamespace(points=points) for points in responses
    ]
    return client


def test_seed_selection_prefers_likes_and_playtime_and_is_deterministic(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_vector_size", 2)
    games = [
        game(50, [1.0, 0.0], 1000, "disliked"),
        game(30, [1.0, 0.0], 100, "liked"),
        game(20, [1.0, 0.0], 100, "liked"),
        game(40, [1.0, 0.0], 1000),
        game(60, [1.0, 0.0], 100, "neutral"),
        game(10, [], 10000, "liked"),
        game(70, [1.0, 0.0], 0),
    ]

    selected = user_recommendation.select_representative_seed_games(games, 3)

    assert [item.steam_app_id for item in selected] == [20, 30, 40]
    assert [item.steam_app_id for item in user_recommendation.select_representative_seed_games(games, 8)] == [
        20, 30, 40, 60
    ]


@pytest.mark.asyncio
async def test_seed_loader_uses_one_query_and_only_current_embedded_games(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_vector_size", 2)
    rows = [
        (10, 100, "liked", [1.0, 0.0]),
        (20, 200, "disliked", [0.0, 1.0]),
    ]
    db = SimpleNamespace(
        no_autoflush=nullcontext(),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
    )

    seeds = await user_recommendation.get_representative_seed_games(db, "synthetic-user")

    assert [seed.steam_app_id for seed in seeds] == [10]
    db.execute.assert_awaited_once()
    query = db.execute.await_args.args[0]
    assert "game_embeddings" in str(query)
    assert settings.embeddings_model_name in query.compile().params.values()
    assert settings.embeddings_model_revision in query.compile().params.values()


@pytest.mark.asyncio
async def test_merges_filters_and_rescores_to_profile(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_vector_size", 2)
    seeds = [game(1, [1.0, 0.0], 100, "liked"), game(2, [0.0, 1.0], 100, "liked")]
    seed_loader = AsyncMock(return_value=seeds)
    vector_loader = AsyncMock(return_value={
        10: [1.0, 0.0],
        20: [0.0, 1.0],
        30: [0.6, 0.8],
        40: [1.0, 0.0],
    })
    monkeypatch.setattr(user_recommendation, "get_representative_seed_games", seed_loader)
    monkeypatch.setattr(user_recommendation, "fetch_game_vectors", vector_loader)
    db = SimpleNamespace()
    client = qdrant_client([
        [point(10, 0.8, "Profile hit"), point(1, 0.9, "Owned")],
        [point(10, 0.99, "Duplicate"), point(20, 0.99, "Seed A hit"), point(40, 0.7, "Other")],
        [point(30, 0.01, "Seed B hit"), point(50, 0.95, "Missing vector")],
    ])

    result = await user_recommendation.get_multi_source_recommendation_candidates(
        db=db, client=client, steamid64="synthetic-user",
        user_profile_vector=[1.0, 0.0], owned_ids={1, 2},
        profile_candidate_k=2, seed_game_count=2, seed_candidate_k=3,
    )

    assert result.profile_count == 2
    assert result.seed_ids == [1, 2]
    assert result.seed_candidate_count == 5
    assert result.merged_count == 6
    assert result.after_owned_count == 5
    assert [item["steam_app_id"] for item in result.candidates] == [10, 20, 40, 30]
    assert [item["score"] for item in result.candidates] == pytest.approx([1.0, 0.0, 1.0, 0.6])
    assert result.candidates[0]["name"] == "Profile hit"
    vector_loader.assert_awaited_once_with(db, [10, 20, 40, 30, 50])
    assert client.query_points.await_count == 3
    assert [call.kwargs["query"] for call in client.query_points.await_args_list] == [
        [1.0, 0.0], [1.0, 0.0], [0.0, 1.0]
    ]

    ranked = rank_candidates(result.candidates, {}, {})
    assert [item.steam_app_id for item in ranked] == [10, 40, 30, 20]
    assert ranked[2].similarity_score == pytest.approx(0.6)
    assert ranked[2].hybrid_score == pytest.approx(0.6 * ranked[0].hybrid_score)


@pytest.mark.asyncio
async def test_no_seeds_uses_profile_query_only(monkeypatch):
    monkeypatch.setattr(user_recommendation, "get_representative_seed_games", AsyncMock(return_value=[]))
    vector_loader = AsyncMock(return_value={10: [1.0, 0.0]})
    monkeypatch.setattr(user_recommendation, "fetch_game_vectors", vector_loader)
    client = qdrant_client([[point(10, 0.4, "Profile only")]])

    result = await user_recommendation.get_multi_source_recommendation_candidates(
        SimpleNamespace(), client, "synthetic-user", [1.0, 0.0], set()
    )

    assert result.seed_ids == []
    assert result.seed_candidate_count == 0
    assert result.candidates == [{"steam_app_id": 10, "name": "Profile only", "score": 1.0}]
    client.query_points.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_sources_skip_vector_fetch(monkeypatch):
    monkeypatch.setattr(user_recommendation, "get_representative_seed_games", AsyncMock(return_value=[]))
    vector_loader = AsyncMock(return_value=None)
    monkeypatch.setattr(user_recommendation, "fetch_game_vectors", vector_loader)
    client = qdrant_client([[]])
    db = SimpleNamespace()

    result = await user_recommendation.get_multi_source_recommendation_candidates(
        db, client, "synthetic-user", [1.0, 0.0], set()
    )

    assert result.candidates == []
    assert result.merged_count == result.after_owned_count == 0
    vector_loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_query_failure_propagates(monkeypatch):
    monkeypatch.setattr(user_recommendation, "get_representative_seed_games", AsyncMock(
        return_value=[game(1, [1.0, 0.0], 100, "liked")]
    ))
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.side_effect = [
        SimpleNamespace(points=[]), RuntimeError("Qdrant unavailable")
    ]

    with pytest.raises(RuntimeError, match="Qdrant unavailable"):
        await user_recommendation.get_multi_source_recommendation_candidates(
            SimpleNamespace(), client, "synthetic-user", [1.0, 0.0], set()
        )
