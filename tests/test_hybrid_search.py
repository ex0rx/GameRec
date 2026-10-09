"""Search ranking uses one Qdrant pool and batched Phase 8 metadata helpers."""

from math import log1p
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gamerec.services import hybrid_search
from gamerec.services.hybrid_user_recommendation import (
    calculate_review_quality,
)

pytestmark = pytest.mark.asyncio


def candidate(app_id, similarity, name="Game"):
    return {"steam_app_id": app_id, "score": similarity, "name": name}


def metadata_row(app_id, reviews, positive):
    return SimpleNamespace(
        steam_app_id=app_id,
        total_reviews=reviews,
        total_positive=positive,
        total_negative=reviews - positive,
        genres=None,
        categories=None,
        release_date=None,
    )


@pytest.fixture
def qdrant_search(monkeypatch):
    search = AsyncMock()
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    return search


async def test_scores_filtering_batch_reads_and_top_k(fake_db, qdrant_search):
    qdrant_search.return_value = [
        candidate(3, 0.95, "Too few reviews"),
        candidate(1, 0.8, "First"),
        candidate(2, 0.7, "Second"),
    ]
    fake_db.execute.side_effect = [
        SimpleNamespace(
            fetchall=lambda: [
                metadata_row(1, 100, 90),
                metadata_row(2, 1000, 900),
                metadata_row(3, 10, 9),
            ]
        ),
        SimpleNamespace(scalar_one=lambda: 1000),
    ]
    embedding = [1.0] + [0.0] * 383

    client = object()
    result = await hybrid_search.hybrid_search_games(
        fake_db, client, embedding, top_k=1
    )

    qdrant_search.assert_awaited_once_with(client, embedding, 1000)
    assert len(result.raw_candidates) == 3
    assert result.eligible_count == 2
    assert result.eligible_ids == {1, 2}
    assert result.missing_metadata_ids == set()
    assert len(result.ranked) == 1
    winner = result.ranked[0]
    expected_popularity = log1p(100) / log1p(1000)
    expected_quality = calculate_review_quality(90, 10, 100)
    assert winner.steam_app_id == 1
    assert winner.similarity_score == 0.8
    assert winner.popularity_score == pytest.approx(expected_popularity)
    assert winner.review_quality == pytest.approx(expected_quality)
    assert winner.hybrid_score == pytest.approx(
        0.70 * 0.8 + 0.15 * expected_popularity + 0.15 * expected_quality
    )
    assert fake_db.execute.await_count == 2


async def test_empty_pool_does_not_query_postgres(fake_db, qdrant_search):
    qdrant_search.return_value = []
    result = await hybrid_search.hybrid_search_games(fake_db, object(), [0.0] * 384)
    assert result.raw_candidates == []
    assert result.ranked == []
    assert result.eligible_count == 0
    fake_db.execute.assert_not_awaited()


async def test_missing_metadata_and_review_filter_are_distinct(fake_db, qdrant_search):
    qdrant_search.return_value = [candidate(1, 0.9), candidate(2, 0.8)]
    fake_db.execute.return_value = SimpleNamespace(
        fetchall=lambda: [metadata_row(2, 5, 5)]
    )
    result = await hybrid_search.hybrid_search_games(fake_db, object(), [0.0] * 384)
    assert result.missing_metadata_ids == {1}
    assert result.eligible_ids == set()
    assert result.ranked == []
    fake_db.execute.assert_awaited_once()


async def test_deterministic_ties_and_configurable_weights(fake_db, qdrant_search):
    qdrant_search.return_value = [
        candidate(3, 0.6),
        candidate(2, 0.7),
        candidate(1, 0.7),
    ]
    fake_db.execute.side_effect = [
        SimpleNamespace(
            fetchall=lambda: [
                metadata_row(1, 0, 0),
                metadata_row(2, 0, 0),
                metadata_row(3, 0, 0),
            ]
        ),
        SimpleNamespace(scalar_one=lambda: 0),
    ]
    result = await hybrid_search.hybrid_search_games(
        fake_db,
        object(),
        [0.0] * 384,
        min_total_reviews=0,
        similarity_weight=0.0,
        popularity_weight=1.0,
        review_quality_weight=0.0,
    )
    assert [game.steam_app_id for game in result.ranked] == [1, 2, 3]
    assert all(game.hybrid_score == 0.0 for game in result.ranked)


@pytest.mark.parametrize(
    "overrides",
    [
        {"similarity_weight": -0.1, "popularity_weight": 0.95},
        {"similarity_weight": float("nan")},
        {"review_quality_weight": float("inf")},
        {"similarity_weight": 0.5},
        {"candidate_k": 1001},
        {"top_k": 0},
        {"min_total_reviews": -1},
    ],
)
async def test_invalid_options_do_no_io(fake_db, qdrant_search, overrides):
    with pytest.raises(ValueError):
        await hybrid_search.hybrid_search_games(
            fake_db, object(), [0.0] * 384, **overrides
        )
    qdrant_search.assert_not_awaited()
    fake_db.execute.assert_not_awaited()
