"""Structured filters narrow one batched Phase 9C candidate pool."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gamerec.services import hybrid_search
from gamerec.services.search_filters import SearchFilters

pytestmark = pytest.mark.asyncio


def candidate(app_id, score):
    return {"steam_app_id": app_id, "name": f"Game {app_id}", "score": score}


def row(
    app_id,
    *,
    reviews=200,
    genres=None,
    categories=None,
    release_date=None,
):
    return SimpleNamespace(
        steam_app_id=app_id,
        total_reviews=reviews,
        total_positive=reviews,
        total_negative=0,
        genres=genres,
        categories=categories,
        release_date=release_date,
    )


async def test_no_filters_preserves_phase9c_ranking(fake_db, monkeypatch):
    search = AsyncMock(return_value=[candidate(2, 0.7), candidate(1, 0.8)])
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    rows = [row(1), row(2)]
    fake_db.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: rows),
        SimpleNamespace(scalar_one=lambda: 1000),
        SimpleNamespace(fetchall=lambda: rows),
        SimpleNamespace(scalar_one=lambda: 1000),
    ]

    baseline = await hybrid_search.hybrid_search_games(fake_db, object(), [0.0] * 384)
    unrestricted = await hybrid_search.hybrid_search_games(
        fake_db, object(), [0.0] * 384, filters=SearchFilters()
    )

    assert baseline.ranked == unrestricted.ranked
    assert baseline.eligible_count == unrestricted.eligible_count == 2
    assert baseline.structured_count == unrestricted.structured_count == 2
    assert baseline.returned_count == unrestricted.returned_count == 2
    assert search.await_count == 2
    assert fake_db.execute.await_count == 4


async def test_counts_filtering_and_no_n_plus_one_queries(fake_db, monkeypatch):
    search = AsyncMock(
        return_value=[
            candidate(1, 0.9),
            candidate(2, 0.8),
            candidate(3, 0.7),
            candidate(4, 0.6),
        ]
    )
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    fake_db.execute.side_effect = [
        SimpleNamespace(
            fetchall=lambda: [
                row(2, reviews=50),
                row(
                    3,
                    reviews=500,
                    genres=["Action"],
                    categories=["Co-op"],
                    release_date=date(2020, 1, 1),
                ),
                row(
                    4,
                    reviews=600,
                    genres=["Action"],
                    categories=["Single-player"],
                    release_date=date(2021, 1, 1),
                ),
            ]
        ),
        SimpleNamespace(scalar_one=lambda: 1000),
    ]
    filters = SearchFilters(
        genres=["action"],
        categories=["CO-OP"],
        release_year_from=2020,
        min_reviews=500,
    )

    result = await hybrid_search.hybrid_search_games(
        fake_db, object(), [0.0] * 384, filters=filters, top_k=10
    )

    assert result.retrieved_count == 4
    assert result.metadata_count == 3
    assert result.eligible_count == 2
    assert result.structured_count == 1
    assert result.returned_count == 1
    assert [game.steam_app_id for game in result.ranked] == [3]
    assert fake_db.execute.await_count == 2
    assert search.await_count == 1


async def test_filter_cannot_weaken_existing_review_eligibility(fake_db, monkeypatch):
    search = AsyncMock(return_value=[candidate(1, 0.9), candidate(2, 0.8)])
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    fake_db.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: [row(1, reviews=50), row(2, reviews=200)]),
        SimpleNamespace(scalar_one=lambda: 1000),
    ]
    result = await hybrid_search.hybrid_search_games(
        fake_db, object(), [0.0] * 384, filters=SearchFilters(min_reviews=0)
    )
    assert result.eligible_count == result.structured_count == 1
    assert [game.steam_app_id for game in result.ranked] == [2]


async def test_empty_filtered_results_do_not_relax_constraints(fake_db, monkeypatch):
    search = AsyncMock(return_value=[candidate(1, 0.9)])
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    fake_db.execute.return_value = SimpleNamespace(
        fetchall=lambda: [row(1, genres=["Action"])]
    )
    result = await hybrid_search.hybrid_search_games(
        fake_db, object(), [0.0] * 384, filters=SearchFilters(genres=["Strategy"])
    )
    assert result.retrieved_count == result.metadata_count == result.eligible_count == 1
    assert result.structured_count == result.returned_count == 0
    fake_db.execute.assert_awaited_once()
    search.assert_awaited_once()


async def test_filtered_ranking_keeps_ties_and_top_k(fake_db, monkeypatch):
    search = AsyncMock(return_value=[candidate(2, 0.8), candidate(1, 0.8)])
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    fake_db.execute.side_effect = [
        SimpleNamespace(
            fetchall=lambda: [row(1, genres=["Action"]), row(2, genres=["Action"])]
        ),
        SimpleNamespace(scalar_one=lambda: 200),
    ]
    result = await hybrid_search.hybrid_search_games(
        fake_db,
        object(),
        [0.0] * 384,
        top_k=1,
        filters=SearchFilters(genres=["Action"]),
    )
    assert result.structured_count == 2
    assert result.returned_count == 1
    assert [game.steam_app_id for game in result.ranked] == [1]


async def test_filter_preserves_scores_and_order_of_survivors(fake_db, monkeypatch):
    search = AsyncMock(
        return_value=[candidate(1, 0.9), candidate(2, 0.8), candidate(3, 0.7)]
    )
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    rows = [
        row(1, genres=["Strategy"]),
        row(2, genres=["Action"]),
        row(3, genres=["Action"]),
    ]
    fake_db.execute.side_effect = [
        SimpleNamespace(fetchall=lambda: rows),
        SimpleNamespace(scalar_one=lambda: 1000),
        SimpleNamespace(fetchall=lambda: rows),
        SimpleNamespace(scalar_one=lambda: 1000),
    ]
    unfiltered = await hybrid_search.hybrid_search_games(fake_db, object(), [0.0] * 384)
    filtered = await hybrid_search.hybrid_search_games(
        fake_db, object(), [0.0] * 384, filters=SearchFilters(genres=["Action"])
    )
    assert filtered.ranked == [
        game for game in unfiltered.ranked if game.steam_app_id in {2, 3}
    ]
    assert fake_db.execute.await_count == 4


async def test_mutated_filter_is_revalidated_before_io(fake_db, monkeypatch):
    search = AsyncMock()
    monkeypatch.setattr(hybrid_search, "search_games_by_embedding", search)
    filters = SearchFilters(genres=["Action"])
    filters.genres.append(" ")
    with pytest.raises(ValueError, match="genres"):
        await hybrid_search.hybrid_search_games(
            fake_db, object(), [0.0] * 384, filters=filters
        )
    search.assert_not_awaited()
    fake_db.execute.assert_not_awaited()
