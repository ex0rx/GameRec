"""Unit tests for profile weighting and its database boundary."""

from contextlib import nullcontext
from math import hypot, log1p
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gamerec.core.config import settings
from gamerec.services import user_profile
from gamerec.services.user_profile import UserGameProfile


def game(appid, vector, minutes=0, preference=None):
    return UserGameProfile(appid, vector, minutes, preference)


def test_playtime_uses_log_weighting_and_normalizes_result():
    games = [
        game(10, [1.0, 0.0, 0.0], 300, "liked"),
        game(20, [0.0, 1.0, 0.0], 3000, "liked"),
        game(30, [0.0, 0.0, 1.0], 300, "liked"),
    ]

    profile = user_profile.construct_profile_vector(games, dimensions=3)

    assert profile is not None
    assert len(profile) == 3
    assert hypot(*profile) == pytest.approx(1.0)
    assert profile[1] / profile[0] == pytest.approx(log1p(3000) / log1p(300))
    assert profile[1] / profile[0] < 3000 / 300


def test_dislike_contributes_negatively_and_missing_rating_uses_default():
    games = [
        game(10, [1.0, 0.0, 0.0], 300, "liked"),
        game(20, [0.0, 1.0, 0.0], 300, "disliked"),
        game(30, [0.0, 0.0, 1.0], 300),
    ]

    profile = user_profile.construct_profile_vector(games, dimensions=3)

    assert profile is not None
    assert profile[0] > 0 > profile[1]
    assert profile[2] > 0
    assert profile[1] / profile[0] == pytest.approx(
        user_profile.PREFERENCE_WEIGHTS["disliked"]
        / user_profile.PREFERENCE_WEIGHTS["liked"]
    )
    assert profile[2] / profile[0] == pytest.approx(
        user_profile.UNRATED_WEIGHT / user_profile.PREFERENCE_WEIGHTS["liked"]
    )


def test_weak_playtime_uses_preference_only_fallback():
    games = [
        game(10, [1.0, 0.0], 10000, "liked"),
        game(20, [0.0, 1.0], 0, "neutral"),
    ]

    profile = user_profile.construct_profile_vector(games, dimensions=2)

    assert profile is not None
    assert profile[0] / profile[1] == pytest.approx(
        user_profile.PREFERENCE_WEIGHTS["liked"]
        / user_profile.PREFERENCE_WEIGHTS["neutral"]
    )


def test_weak_playtime_without_preferences_uses_unweighted_library():
    games = [game(10, [1.0, 0.0], 10000), game(20, [0.0, 1.0], 0)]

    assert user_profile.construct_profile_vector(games, dimensions=2) == pytest.approx(
        [2**-0.5, 2**-0.5]
    )


def test_missing_stored_playtime_uses_unweighted_fallback():
    games = [game(10, [1.0, 0.0], None), game(20, [0.0, 1.0], 0)]

    assert user_profile.construct_profile_vector(games, dimensions=2) == pytest.approx(
        [2**-0.5, 2**-0.5]
    )


@pytest.mark.parametrize(
    "games",
    [
        [],
        [game(10, [0.0, 0.0])],
        [game(10, [1.0, 0.0], 300, "disliked")],
        [game(10, [1.0, 0.0], 300), game(20, [-1.0, 0.0], 300)],
        [game(10, [float("nan"), 0.0], 300)],
    ],
)
def test_no_meaningful_profile_returns_none(games):
    assert user_profile.construct_profile_vector(games, dimensions=2) is None


def test_invalid_vector_is_skipped_before_playtime_evidence():
    games = [
        game(10, [1.0, 0.0], 300),
        game(20, [0.0, 1.0], 0),
        game(30, [1.0], 10000),
    ]

    assert user_profile.construct_profile_vector(games, dimensions=2) == pytest.approx(
        [2**-0.5, 2**-0.5]
    )


def fake_db(*rowsets):
    results = [SimpleNamespace(all=lambda rows=rows: rows) for rows in rowsets]
    return SimpleNamespace(
        no_autoflush=nullcontext(), execute=AsyncMock(side_effect=results)
    )


@pytest.mark.asyncio
async def test_build_profile_preserves_all_owned_ids_and_batches_embedded_games(
    monkeypatch,
):
    monkeypatch.setattr(settings, "embeddings_vector_size", 2)
    owned_rows = [(appid, 0, None) for appid in range(1, 62)]
    db = fake_db(owned_rows, [(1, [1.0, 0.0]), (60, [0.0, 1.0])])

    result = await user_profile.build_user_profile_vector(db, "synthetic-user")

    assert result is not None
    vector, owned_ids = result
    assert vector == pytest.approx([2**-0.5, 2**-0.5])
    assert owned_ids == set(range(1, 62))
    assert db.execute.await_count == 2
    owned_query, vector_query = [call.args[0] for call in db.execute.await_args_list]
    assert "LIMIT" not in str(owned_query)
    assert "LEFT OUTER JOIN" in str(owned_query)
    assert "synthetic-user" in owned_query.compile().params.values()
    assert sorted(vector_query.compile().params["steam_app_id_1"]) == list(range(1, 62))
    assert settings.embeddings_model_name in vector_query.compile().params.values()
    assert settings.embeddings_model_revision in vector_query.compile().params.values()


@pytest.mark.asyncio
async def test_missing_library_skips_embedding_query():
    db = fake_db([])

    assert await user_profile.build_user_profile_vector(db, "synthetic-missing") is None
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unembedded_library_has_no_profile():
    db = fake_db([(10, 300, "liked")], [])

    assert await user_profile.build_user_profile_vector(db, "synthetic-user") is None
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_empty_vector_request_skips_database():
    db = fake_db()

    assert await user_profile.fetch_game_vectors(db, []) is None
    db.execute.assert_not_awaited()
