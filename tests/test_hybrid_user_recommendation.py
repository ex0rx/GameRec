"""Hybrid scoring with synthetic metadata and deterministic reference dates."""

from datetime import date, timedelta
from itertools import pairwise
from math import isfinite, log1p
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamerec.services.hybrid_user_recommendation import (
    DAYS_PER_YEAR,
    RECENCY_HALF_LIFE_YEARS,
    CandidateAffinityScores,
    CandidateRankingFeatures,
    CandidateRankingMetadata,
    UserGameAffinityMetadata,
    build_candidate_affinity_scores,
    build_ranking_features,
    build_user_affinity_profile,
    calculate_genre_scores,
    calculate_release_recency,
    capped_log_playtime_weights,
    get_candidate_ranking_metadata,
    get_global_genre_statistics,
    rank_candidates,
)

AS_OF = date(2030, 7, 1)


def metadata(appid=1, *, genres=None, release_date=None):
    return CandidateRankingMetadata(
        steam_app_id=appid,
        total_reviews=100,
        total_positive=90,
        total_negative=10,
        genres=genres,
        categories=None,
        release_date=release_date,
    )


def played(appid, genres, *, preference="liked", minutes=100, categories=None):
    return UserGameAffinityMetadata(appid, minutes, preference, genres, categories)


def test_capped_playtime_empty_zero_negative_and_single_game():
    assert capped_log_playtime_weights([]) == []
    assert capped_log_playtime_weights([0, None, -20]) == [0.0, 0.0, 0.0]
    assert capped_log_playtime_weights([None, 100, -2]) == pytest.approx(
        [0.0, log1p(100), 0.0]
    )


def test_capped_playtime_interpolates_and_preserves_input_order():
    playtimes = [10_000, 0, 10, 100, -1]
    weights = capped_log_playtime_weights(playtimes, percentile=50)

    assert weights == pytest.approx([log1p(100), 0, log1p(10), log1p(100), 0])
    assert weights == capped_log_playtime_weights(playtimes, percentile=50)
    assert all(isfinite(weight) and weight >= 0 for weight in weights)
    assert capped_log_playtime_weights([10, 100, 10_000], percentile=100) == pytest.approx(
        [log1p(10), log1p(100), log1p(10_000)]
    )


def test_p90_caps_outlier_but_leaves_lower_playtimes_unchanged():
    playtimes = [10, *([100] * 9), 10**9]
    weights = capped_log_playtime_weights(playtimes)

    assert weights[0] == pytest.approx(log1p(10))
    assert weights[1:10] == pytest.approx([log1p(100)] * 9)
    assert weights[-1] == pytest.approx(log1p(100))
    assert all(isfinite(weight) and weight >= 0 for weight in weights)
    ordinary = capped_log_playtime_weights([10, 100, 1_000, 10_000])
    assert ordinary[0] < ordinary[1] < ordinary[2] < ordinary[3]


@pytest.mark.parametrize("percentile", [0, -1, 101, float("nan"), float("inf")])
def test_capped_playtime_rejects_invalid_percentile(percentile):
    with pytest.raises(ValueError, match="percentile"):
        capped_log_playtime_weights([100], percentile=percentile)


def test_p90_limits_outlier_genre_and_category_influence():
    games = [
        played(i, ["Strategy"], categories=["Co-op"]) for i in range(1, 10)
    ]
    games += [
        played(10, ["RPG"], categories=["Crafting"]),
        played(11, ["Action"], minutes=10**9, categories=["Single-player"]),
    ]
    kwargs = {"total_games": 0, "genre_counts": {}}
    capped = build_user_affinity_profile(games, **kwargs)
    uncapped = build_user_affinity_profile(games, playtime_percentile=None, **kwargs)
    assert build_user_affinity_profile(games, playtime_percentile=100, **kwargs) == uncapped

    assert capped.genres_score["Strategy"] == uncapped.genres_score["Strategy"] == 1.0
    assert 0 < capped.genres_score["Action"] < uncapped.genres_score["Action"]
    assert capped.genres_score["RPG"] > 0
    assert capped.categories_score["Single-player"] < uncapped.categories_score["Single-player"]
    assert capped.categories_score["Crafting"] > 0
    candidate = metadata(12, genres=["Action"])
    assert build_candidate_affinity_scores({12: candidate}, capped)[12].genres_affinity == pytest.approx(
        capped.genres_score["Action"]
    )


def test_capped_affinity_preserves_preference_signs_and_empty_metadata():
    profile = build_user_affinity_profile(
        [
            played(1, ["Action"], preference="liked", categories=["Co-op"]),
            played(2, ["Puzzle"], preference="disliked", categories=["Solo"]),
            played(3, ["RPG"], preference=None, categories=["Crafting"]),
            played(4, ["Adventure"], preference="neutral", categories=["Exploration"]),
            played(5, None, minutes=100, categories=None),
            played(6, ["No-play"], minutes=0, categories=["None"]),
        ],
        total_games=0,
        genre_counts={},
    )

    assert profile.genres_score["Action"] == 1.0
    assert profile.genres_score["Puzzle"] < 0 < profile.genres_score["RPG"]
    assert profile.categories_score == pytest.approx({
        "Co-op": 1.0, "Solo": -0.6, "Crafting": 0.5, "Exploration": 0.4,
    })
    assert "No-play" not in profile.genres_score


@pytest.mark.parametrize(
    "release, expected",
    [
        (None, 0.0),
        (AS_OF, 1.0),
        (AS_OF + timedelta(days=365), 1.0),
        (AS_OF - timedelta(days=round(RECENCY_HALF_LIFE_YEARS * DAYS_PER_YEAR)), 0.5),
        (AS_OF - timedelta(days=round(2 * RECENCY_HALF_LIFE_YEARS * DAYS_PER_YEAR)), 0.25),
    ],
)
def test_recency_half_life_missing_future_and_bounds(release, expected):
    score = calculate_release_recency(release, as_of=AS_OF)
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(expected, abs=0.0001)


def test_recency_is_near_one_this_year_and_decays_smoothly():
    assert calculate_release_recency(date(2030, 1, 1), as_of=AS_OF) > 0.96
    releases = [AS_OF - timedelta(days=days) for days in (0, 100, 101, 1000, 10000)]
    scores = [calculate_release_recency(release, as_of=AS_OF) for release in releases]
    assert all(newer > older for newer, older in pairwise(scores))
    assert 0 < scores[1] - scores[2] < 0.001
    assert calculate_release_recency(
        AS_OF - timedelta(days=2922),
        as_of=AS_OF,
        half_life_years=8,
    ) == pytest.approx(0.5)


@pytest.mark.parametrize("half_life", [0, -1, float("nan"), float("inf")])
def test_recency_rejects_invalid_half_life(half_life):
    with pytest.raises(ValueError, match="half_life_years"):
        calculate_release_recency(AS_OF, as_of=AS_OF, half_life_years=half_life)


def test_idf_discounts_common_genres_without_inverting_positive_evidence():
    scores = calculate_genre_scores(
        {"Action": 10, "Strategy": 10},
        total_games=1000,
        genre_counts={"Action": 1000, "Strategy": 50},
    )
    assert 0 < scores["Action"] < scores["Strategy"]
    assert scores["Action"] == pytest.approx(log1p(10))


def test_exposure_increases_score_with_diminishing_returns():
    scores = [
        calculate_genre_scores(
            {"Strategy": exposure},
            total_games=100,
            genre_counts={"Strategy": 10},
        )["Strategy"]
        for exposure in (1, 2, 3, 10)
    ]
    assert scores[0] < scores[1] < scores[2] < scores[3]
    assert scores[2] - scores[1] < scores[1] - scores[0]
    assert scores[3] < 10 * scores[0]


def test_profile_limits_broad_genres_and_retains_category_scoring():
    games = [played(i, ["Action"], categories=["Single-player"]) for i in range(20)]
    games += [played(100, ["Strategy"], categories=["Co-op"])]
    profile = build_user_affinity_profile(
        games,
        total_games=1000,
        genre_counts={"Action": 900, "Strategy": 10},
    )
    # Twenty times the exposure no longer means twenty times the affinity.
    assert 0 < profile.genres_score["Action"] < profile.genres_score["Strategy"] == 1
    assert profile.categories_score == {"Single-player": 1.0, "Co-op": 0.05}


def test_multiple_meaningful_matches_outrank_broad_genre_only():
    profile = build_user_affinity_profile(
        [played(1, ["Action", "Strategy", "RPG"])],
        total_games=1000,
        genre_counts={"Action": 900, "Strategy": 50, "RPG": 50},
    )
    scores = build_candidate_affinity_scores(
        {
            2: metadata(2, genres=["Action"]),
            3: metadata(3, genres=["Strategy", "RPG"]),
            4: metadata(4, genres=["Strategy", "Unknown"]),
            5: metadata(5),
        },
        profile,
    )
    assert scores[3].genres_affinity == 1.0
    assert scores[3].affinity_score == pytest.approx(0.7)
    assert scores[3].genres_affinity > scores[2].genres_affinity > 0
    assert scores[4].genres_affinity == 0.5
    assert scores[5].affinity_score == 0.0


def test_duplicate_and_missing_genres_do_not_inflate_profile():
    kwargs = {"total_games": 100, "genre_counts": {"Action": 90, "RPG": 10}}
    expected = build_user_affinity_profile([played(1, ["Action", "RPG"])], **kwargs)
    actual = build_user_affinity_profile(
        [played(1, ["Action", "Action", "RPG", "", "  "]), played(2, None)],
        **kwargs,
    )
    assert actual == expected
    assert build_user_affinity_profile([], **kwargs).genres_score == {}


def test_dislikes_remain_negative_and_cancel_positive_evidence_safely():
    profile = build_user_affinity_profile(
        [played(1, ["Action"], preference="disliked"), played(2, ["RPG"])],
        total_games=100,
        genre_counts={"Action": 90, "RPG": 10},
    )
    assert -1 <= profile.genres_score["Action"] < 0 < profile.genres_score["RPG"] <= 1
    assert all(isfinite(score) for score in profile.genres_score.values())
    scores = calculate_genre_scores(
        {"Negative": -10, "Cancelled": 0, "Positive": 10},
        total_games=0,
        genre_counts={},
    )
    assert scores == pytest.approx(
        {"Negative": -log1p(10), "Cancelled": 0, "Positive": log1p(10)},
    )


def test_non_gameplay_labels_are_excluded_without_changing_categories():
    profile = build_user_affinity_profile(
        [
            played(1, ["Action", "Free To Play"], categories=["Co-op"]),
            played(2, ["Free To Play", "Early Access"], categories=["Single-player"]),
        ],
        total_games=10,
        genre_counts={"Action": 10},
    )
    assert profile.genres_score == {"Action": 1.0}
    assert profile.categories_score == {"Co-op": 1.0, "Single-player": 1.0}
    assert (
        build_user_affinity_profile(
            [played(3, ["Free To Play", "Early Access"])],
            total_games=0,
            genre_counts={},
        ).genres_score
        == {}
    )


def test_excluded_candidate_labels_neither_boost_nor_dilute_affinity():
    profile = build_user_affinity_profile(
        [played(1, ["Action"])],
        total_games=10,
        genre_counts={"Action": 10},
    )
    scores = build_candidate_affinity_scores(
        {
            2: metadata(2, genres=["Action"]),
            3: metadata(3, genres=["Action", "Free To Play", "Early Access"]),
            4: metadata(4, genres=["Free To Play", "Early Access"]),
        },
        profile,
    )
    assert scores[2].genres_affinity == scores[3].genres_affinity == 1.0
    assert scores[2].affinity_score == scores[3].affinity_score
    assert scores[4].genres_affinity == scores[4].affinity_score == 0.0


def test_zero_playtime_keeps_existing_affinity_behaviour():
    profile = build_user_affinity_profile(
        [played(1, ["Action"], minutes=0), played(2, ["RPG"], minutes=None)],
        total_games=0,
        genre_counts={},
    )
    assert profile.genres_score == profile.categories_score == {}


def test_ranking_exposes_and_combines_all_features():
    items = {1: metadata(release_date=AS_OF)}
    features = build_ranking_features(items, max_total_reviews=100, as_of=AS_OF)
    profile = build_user_affinity_profile(
        [played(10, ["Strategy"])],
        total_games=100,
        genre_counts={"Strategy": 10},
    )
    affinity = build_candidate_affinity_scores(
        {1: metadata(genres=["Strategy"])}, profile
    )
    result = rank_candidates(
        [{"steam_app_id": 1, "name": "Example", "score": 0.8}],
        features,
        affinity,
        similarity_weight=0.55,
        popularity_weight=0.15,
        review_quality_weight=0.15,
        affinity_weight=0.10,
        recency_weight=0.05,
    )[0]
    assert result.recency_score == features[1].recency_score == 1.0
    assert result.affinity_score == pytest.approx(0.7)
    assert result.hybrid_score == pytest.approx(
        0.55 * 0.8 + 0.15 * 1.0 + 0.15 * features[1].review_quality + 0.10 * 0.7 + 0.05,
    )


def test_zero_new_weights_reproduce_previous_scores_and_order():
    candidates = [
        {"steam_app_id": 1, "name": "A", "score": 0.8},
        {"steam_app_id": 2, "name": "B", "score": 0.9},
    ]
    features = {
        1: CandidateRankingFeatures(1, 0.5, 0.7, recency_score=1.0),
        2: CandidateRankingFeatures(2, 0.5, 0.7, recency_score=0.0),
    }
    affinities = {
        1: CandidateAffinityScores(1, 1, 1, 1),
        2: CandidateAffinityScores(2, -1, -1, -1),
    }
    result = rank_candidates(
        candidates,
        features,
        affinities,
        similarity_weight=0.70,
        popularity_weight=0.15,
        review_quality_weight=0.15,
        affinity_weight=0,
        recency_weight=0,
    )
    assert [item.steam_app_id for item in result] == [2, 1]
    for item in result:
        assert item.hybrid_score == pytest.approx(
            0.70 * item.similarity_score + 0.15 * 0.5 + 0.15 * 0.7,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"recency_weight": -0.1, "similarity_weight": 0.70},
        {"affinity_weight": -0.1, "similarity_weight": 0.75},
        {"recency_weight": 0.2},
        {"similarity_weight": float("nan")},
        {"review_quality_weight": float("inf")},
    ],
)
def test_ranking_rejects_invalid_weights(overrides):
    with pytest.raises(ValueError, match="Ranking weights"):
        rank_candidates([], {}, {}, **overrides)


def test_missing_ranking_metadata_uses_zero_auxiliary_scores():
    result = rank_candidates(
        [{"steam_app_id": 1, "name": None, "score": 0.8}],
        {},
        {},
        similarity_weight=0.55,
        popularity_weight=0.15,
        review_quality_weight=0.15,
        affinity_weight=0.10,
        recency_weight=0.05,
    )[0]
    assert result.recency_score == result.affinity_score == 0
    assert result.hybrid_score == pytest.approx(0.55 * 0.8)


@pytest.mark.asyncio
async def test_candidate_metadata_includes_release_date_in_same_query(fake_db):
    item = metadata(release_date=AS_OF)
    fake_db.execute.return_value = Mock(fetchall=Mock(return_value=[item]))
    assert await get_candidate_ranking_metadata(fake_db, [1]) == {1: item}
    fake_db.execute.assert_awaited_once()
    assert "games.release_date" in str(fake_db.execute.call_args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_global_genre_statistics_uses_one_query(fake_db, empty):
    rows = (
        []
        if empty
        else [
            SimpleNamespace(genre="Action", game_count=3, total_games=4),
            SimpleNamespace(genre="RPG", game_count=2, total_games=4),
        ]
    )
    fake_db.execute.return_value = Mock(all=Mock(return_value=rows))
    assert await get_global_genre_statistics(fake_db) == (
        (0, {}) if empty else (4, {"Action": 3, "RPG": 2})
    )
    fake_db.execute.assert_awaited_once()
