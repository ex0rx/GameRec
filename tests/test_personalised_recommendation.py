"""The HTTP orchestration reuses existing ranking and owns client cleanup."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gamerec.services import personalised_recommendation as service
from gamerec.services.hybrid_user_recommendation import CandidateRankingMetadata
from gamerec.services.user_game_preferences import UserNotFoundError

pytestmark = pytest.mark.asyncio
USER = "76561190000000001"  # Synthetic fixture, not a configured account.


@pytest.fixture
def pipeline(monkeypatch, fake_db):
    # AsyncSession.execute is awaited, but its buffered Result methods are synchronous.
    fake_db.execute.return_value = Mock()
    fake_db.execute.return_value.scalar_one_or_none.return_value = USER
    client = SimpleNamespace(close=AsyncMock())
    factory = Mock(return_value=client)
    profile = AsyncMock(return_value=([1.0, 0.0], {10}))
    candidates = [
        {"steam_app_id": 20, "name": None, "score": 0.9},
        {"steam_app_id": 30, "name": "Another game", "score": 0.6},
        {"steam_app_id": 40, "name": "Too few reviews", "score": 0.99},
        {"steam_app_id": 50, "name": "Missing metadata", "score": 0.99},
    ]
    generation = AsyncMock(return_value=SimpleNamespace(candidates=candidates))
    metadata = {
        appid: CandidateRankingMetadata(
            appid, count, count, 0, ["Action"], None, date(2020, 1, 1)
        )
        for appid, count in ((20, 1000), (30, 500), (40, 499))
    }
    helpers = {
        "get_qdrant_client": factory,
        "build_user_profile_vector": profile,
        "get_multi_source_recommendation_candidates": generation,
        "get_candidate_ranking_metadata": AsyncMock(return_value=metadata),
        "get_total_max_reviews": AsyncMock(return_value=1000),
        "get_user_affinity_metadata": AsyncMock(return_value=[]),
        "get_global_genre_statistics": AsyncMock(return_value=(100, {"Action": 50})),
        "rank_candidates": Mock(wraps=service.rank_candidates),
    }
    for name, helper in helpers.items():
        monkeypatch.setattr(service, name, helper)
    return SimpleNamespace(client=client, candidates=candidates, **helpers)


async def test_reuses_pipeline_defaults_batch_metadata_and_ranking(fake_db, pipeline):
    ranked = await service.get_user_recommendations(fake_db, USER)
    assert [game.steam_app_id for game in ranked] == [20, 30]
    assert ranked[0].name is None
    assert ranked[0].hybrid_score >= ranked[1].hybrid_score
    pipeline.build_user_profile_vector.assert_awaited_once_with(fake_db, USER)
    pipeline.get_multi_source_recommendation_candidates.assert_awaited_once_with(
        db=fake_db,
        client=pipeline.client,
        steamid64=USER,
        user_profile_vector=[1.0, 0.0],
        owned_ids={10},
        profile_candidate_k=5000,
        seed_game_count=10,
        seed_candidate_k=100,
    )
    pipeline.get_candidate_ranking_metadata.assert_awaited_once_with(
        fake_db, [20, 30, 40, 50]
    )
    pipeline.get_user_affinity_metadata.assert_awaited_once_with(fake_db, USER)
    # No weight override or MMR changes the current Phase 8 ranking.
    args, kwargs = pipeline.rank_candidates.call_args
    assert len(args) == 3 and kwargs == {}
    assert [item["steam_app_id"] for item in args[0]] == [20, 30]
    assert set(args[1]) == set(args[2]) == {20, 30}
    statement = fake_db.execute.call_args.args[0]
    assert statement.get_execution_options()["autoflush"] is False
    assert list(statement.compile().params.values()) == [USER]
    pipeline.client.close.assert_awaited_once()
    fake_db.commit.assert_not_called()


async def test_top_k_preserves_the_existing_order(fake_db, pipeline):
    ranked = await service.get_user_recommendations(fake_db, USER, limit=1)
    assert len(ranked) == 1 and ranked[0].steam_app_id == 20
    pipeline.client.close.assert_awaited_once()


async def test_unknown_user_does_not_build_profile_or_client(fake_db, pipeline):
    fake_db.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(UserNotFoundError):
        await service.get_user_recommendations(fake_db, USER)
    pipeline.build_user_profile_vector.assert_not_called()
    pipeline.get_qdrant_client.assert_not_called()


async def test_no_usable_profile_skips_qdrant(fake_db, pipeline):
    pipeline.build_user_profile_vector.return_value = None
    assert await service.get_user_recommendations(fake_db, USER) == []
    pipeline.get_qdrant_client.assert_not_called()
    pipeline.get_multi_source_recommendation_candidates.assert_not_called()


async def test_no_candidates_skips_metadata_and_closes_client(fake_db, pipeline):
    pipeline.get_multi_source_recommendation_candidates.return_value.candidates = []
    assert await service.get_user_recommendations(fake_db, USER) == []
    pipeline.get_candidate_ranking_metadata.assert_not_called()
    pipeline.client.close.assert_awaited_once()


async def test_missing_metadata_is_empty_not_an_invented_result(fake_db, pipeline):
    pipeline.get_candidate_ranking_metadata.return_value = {}
    assert await service.get_user_recommendations(fake_db, USER) == []
    pipeline.get_total_max_reviews.assert_not_called()
    pipeline.rank_candidates.assert_not_called()
    pipeline.client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "stage",
    [
        "get_multi_source_recommendation_candidates",
        "get_candidate_ranking_metadata",
        "get_total_max_reviews",
        "get_user_affinity_metadata",
        "get_global_genre_statistics",
        "rank_candidates",
    ],
)
async def test_failure_at_each_stage_still_closes_client(fake_db, pipeline, stage):
    getattr(pipeline, stage).side_effect = OSError("synthetic failure")
    with pytest.raises(OSError):
        await service.get_user_recommendations(fake_db, USER)
    pipeline.client.close.assert_awaited_once()


async def test_database_failure_before_client_creation(fake_db, pipeline):
    fake_db.execute.side_effect = OSError("synthetic failure")
    with pytest.raises(OSError):
        await service.get_user_recommendations(fake_db, USER)
    pipeline.get_qdrant_client.assert_not_called()


async def test_close_failure_propagates_to_http_error_handling(fake_db, pipeline):
    pipeline.client.close.side_effect = OSError("synthetic close failure")
    with pytest.raises(OSError):
        await service.get_user_recommendations(fake_db, USER)
    pipeline.client.close.assert_awaited_once()


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
async def test_invalid_service_limit_has_no_io(fake_db, pipeline, limit):
    with pytest.raises(ValueError):
        await service.get_user_recommendations(fake_db, USER, limit=limit)
    fake_db.execute.assert_not_called()
    pipeline.get_qdrant_client.assert_not_called()
