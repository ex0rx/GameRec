"""Signed feedback mathematics; synthetic vectors and no external services."""

from math import hypot, isfinite, log1p

import pytest

from gamerec.core.config import settings
from gamerec.services.hybrid_user_recommendation import (
    UserGameAffinityMetadata,
    build_user_affinity_profile,
)
from gamerec.services.user_profile import (
    UserGameProfile,
    apply_explicit_playtime_floor,
    construct_profile_vector,
)
from gamerec.services.user_recommendation import select_representative_seed_games


def profile_games(minutes, preference):
    return [
        UserGameProfile(10, [1.0, 0.0, 0.0, 0.0], minutes, preference),
        UserGameProfile(20, [0.0, 1.0, 0.0, 0.0], 300, None),
        UserGameProfile(30, [0.0, 0.0, 1.0, 0.0], 300, None),
        UserGameProfile(40, [0.0, 0.0, 0.0, 1.0], 300, None),
    ]


@pytest.mark.parametrize("minutes", [0, 1, 60, 300, 12_000])
def test_like_increases_and_dislike_reverses_profile_contribution(minutes):
    vectors = {
        preference: construct_profile_vector(
            profile_games(minutes, preference), dimensions=4
        )
        for preference in (None, "liked", "disliked")
    }
    baseline, liked, disliked = (vectors[p] for p in (None, "liked", "disliked"))
    assert liked[0] / liked[1] > baseline[0] / baseline[1] >= 0
    assert disliked[0] < 0
    assert disliked[0] / disliked[1] == pytest.approx(
        -0.6 * max(1.0, log1p(minutes)) / (0.5 * log1p(300))
    )
    for vector in vectors.values():
        assert all(isfinite(value) for value in vector)
        assert hypot(*vector) == pytest.approx(1.0)


def test_clearing_restores_original_implicit_profile():
    baseline = construct_profile_vector(profile_games(300, None), dimensions=4)
    liked = construct_profile_vector(profile_games(300, "liked"), dimensions=4)
    restored = construct_profile_vector(profile_games(300, None), dimensions=4)
    assert liked != baseline
    assert restored == baseline


@pytest.mark.parametrize("preference", [None, "neutral"])
def test_explicit_floor_preserves_unrated_and_legacy_neutral(preference):
    assert apply_explicit_playtime_floor(0.0, preference) == 0.0
    assert apply_explicit_playtime_floor(0.5, preference) == 0.5
    assert apply_explicit_playtime_floor(10.0, preference) == 10.0


@pytest.mark.parametrize("preference", ["liked", "disliked"])
def test_explicit_floor_is_monotonic_and_does_not_change_ordinary_playtime(preference):
    assert [
        apply_explicit_playtime_floor(value, preference) for value in (0, 0.5, 1, 2, 10)
    ] == [1, 1, 1, 2, 10]


def test_zero_playtime_affinity_preserves_signed_feedback_and_legacy_semantics():
    profile = build_user_affinity_profile(
        [
            UserGameAffinityMetadata(10, 0, "liked", ["Action"], ["Co-op"]),
            UserGameAffinityMetadata(20, 0, "disliked", ["Puzzle"], ["Solo"]),
            UserGameAffinityMetadata(30, 0, None, ["RPG"], ["Crafting"]),
            UserGameAffinityMetadata(40, 0, "neutral", ["Adventure"], ["Exploration"]),
        ],
        total_games=0,
        genre_counts={},
    )
    assert profile.genres_score["Action"] > 0 > profile.genres_score["Puzzle"]
    assert profile.categories_score["Co-op"] > 0 > profile.categories_score["Solo"]
    assert "RPG" not in profile.genres_score
    assert "Adventure" not in profile.genres_score


def test_zero_playtime_like_is_positive_seed_but_dislike_never_is(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_vector_size", 2)
    games = [
        UserGameProfile(10, [1.0, 0.0], 0, "liked"),
        UserGameProfile(20, [0.0, 1.0], 1, None),
        UserGameProfile(30, [1.0, 0.0], 12_000, "disliked"),
    ]
    assert [game.steam_app_id for game in select_representative_seed_games(games)] == [
        10,
        20,
    ]


def test_only_dislikes_and_cancelled_profiles_remain_safe():
    assert (
        construct_profile_vector(
            [
                UserGameProfile(10, [1.0, 0.0], 12_000, "disliked"),
                UserGameProfile(20, [0.0, 1.0], 0, "disliked"),
            ],
            dimensions=2,
        )
        is None
    )
    assert (
        construct_profile_vector(
            [
                UserGameProfile(10, [1.0, 0.0], 0, None),
                UserGameProfile(20, [-1.0, 0.0], 0, None),
            ],
            dimensions=2,
        )
        is None
    )
    mixed = construct_profile_vector(
        [
            UserGameProfile(10, [1.0, 0.0], 0, "liked"),
            UserGameProfile(20, [1.0, 0.0], 12_000, "disliked"),
        ],
        dimensions=2,
    )
    assert mixed == [1.0, 0.0]  # Sparse-history path remains preference-only.


@pytest.mark.parametrize("minutes", [0, 60, 300, 12_000])
def test_affinity_dislike_sign_survives_playtime_and_capping(minutes):
    profile = build_user_affinity_profile(
        [
            UserGameAffinityMetadata(10, minutes, "disliked", ["Puzzle"], ["Solo"]),
            UserGameAffinityMetadata(20, 300, "liked", ["Action"], ["Co-op"]),
        ],
        total_games=10,
        genre_counts={"Puzzle": 2, "Action": 2},
    )
    assert profile.genres_score["Puzzle"] < 0 < profile.genres_score["Action"]
    assert profile.categories_score["Solo"] < 0 < profile.categories_score["Co-op"]
    assert all(isfinite(value) for value in profile.genres_score.values())
