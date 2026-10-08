"""Explicit search filters match stored metadata without translating labels."""

from datetime import date

import pytest

from gamerec.services.hybrid_user_recommendation import CandidateRankingMetadata
from gamerec.services.search_filters import SearchFilters, matches_search_filters


def metadata(
    *,
    genres=None,
    categories=None,
    release_date=None,
    total_reviews=None,
):
    return CandidateRankingMetadata(
        steam_app_id=1,
        total_reviews=total_reviews,
        total_positive=None,
        total_negative=None,
        genres=genres,
        categories=categories,
        release_date=release_date,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"genres": [""]},
        {"genres": ["Action", "  "]},
        {"genres": [42]},
        {"genres": "Action"},
        {"categories": ["\t"]},
        {"categories": [None]},
        {"release_year_from": 0},
        {"release_year_to": 10000},
        {"release_year_from": True},
        {"release_year_to": 2020.5},
        {"release_year_from": 2025, "release_year_to": 2020},
        {"min_reviews": -1},
        {"min_reviews": True},
        {"min_reviews": "100"},
    ],
)
def test_invalid_filters_rejected(kwargs):
    with pytest.raises(ValueError):
        SearchFilters(**kwargs)


def test_omitted_and_empty_label_filters_are_unrestricted():
    item = metadata()
    assert matches_search_filters(item, SearchFilters())
    assert matches_search_filters(item, SearchFilters(genres=[], categories=[]))


def test_case_insensitive_or_within_groups_and_and_between_groups():
    item = metadata(genres=["Action", "RPG"], categories=["Online Co-op"])
    assert matches_search_filters(
        item,
        SearchFilters(
            genres=["strategy", " action "],
            categories=["local co-op", "ONLINE CO-OP"],
        ),
    )
    assert not matches_search_filters(
        item, SearchFilters(genres=["Action"], categories=["Co-op"])
    )
    assert not matches_search_filters(item, SearchFilters(genres=["Strategy"]))
    assert not matches_search_filters(item, SearchFilters(categories=["LAN Co-op"]))


def test_year_bounds_are_inclusive_and_unknown_dates_do_not_match():
    item = metadata(release_date=date(2020, 6, 15))
    assert matches_search_filters(
        item, SearchFilters(release_year_from=2020, release_year_to=2020)
    )
    assert not matches_search_filters(item, SearchFilters(release_year_from=2021))
    assert not matches_search_filters(item, SearchFilters(release_year_to=2019))
    assert not matches_search_filters(metadata(), SearchFilters(release_year_from=2020))


def test_minimum_reviews_and_missing_counts():
    assert matches_search_filters(
        metadata(total_reviews=100), SearchFilters(min_reviews=100)
    )
    assert not matches_search_filters(
        metadata(total_reviews=99), SearchFilters(min_reviews=100)
    )
    assert not matches_search_filters(metadata(), SearchFilters(min_reviews=1))
    assert matches_search_filters(metadata(), SearchFilters(min_reviews=0))
