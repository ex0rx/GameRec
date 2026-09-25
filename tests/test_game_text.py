"""Embedding text contract tests using in-memory Game objects only."""

import pytest

from gamerec.ml.game_text import build_game_embedding_text
from gamerec.models.game import Game


def test_complete_metadata_has_exact_format():
    game = Game(
        steam_app_id=10,
        name=" \tExample Game\n",
        short_description="\n Explore  worlds and solve puzzles. \t",
        genres=["Strategy", "Adventure"],
        categories=["Single-player", "Co-op"],
    )

    assert build_game_embedding_text(game) == (
        "Title: Example Game\n"
        "Description: Explore  worlds and solve puzzles.\n"
        "Genres: Strategy, Adventure\n"
        "Categories: Single-player, Co-op"
    )


@pytest.mark.parametrize(
    "description, genres, categories, expected",
    [
        pytest.param(
            None,
            ["Adventure"],
            ["Single-player"],
            "Title: Example Game\nGenres: Adventure\nCategories: Single-player",
            id="missing-description",
        ),
        pytest.param(
            "Explore worlds.",
            None,
            ["Single-player"],
            "Title: Example Game\nDescription: Explore worlds.\n"
            "Categories: Single-player",
            id="missing-genres",
        ),
        pytest.param(
            "Explore worlds.",
            ["Adventure"],
            None,
            "Title: Example Game\nDescription: Explore worlds.\nGenres: Adventure",
            id="missing-categories",
        ),
        pytest.param(None, None, None, "Title: Example Game", id="all-missing"),
    ],
)
def test_missing_optional_metadata_is_omitted(
    description, genres, categories, expected
):
    game = Game(
        steam_app_id=10,
        name="Example Game",
        short_description=description,
        genres=genres,
        categories=categories,
    )

    assert build_game_embedding_text(game) == expected


@pytest.mark.parametrize(
    "description", ["", "   ", "\t\n "], ids=["empty", "spaces", "mixed-whitespace"]
)
@pytest.mark.parametrize(
    "name, expected",
    [("Example Game", "Title: Example Game"), (" \t\n", "")],
    ids=["title-only", "all-blank"],
)
def test_empty_metadata_is_omitted(name, description, expected):
    game = Game(
        steam_app_id=10,
        name=name,
        short_description=description,
        genres=[],
        categories=[],
    )

    assert build_game_embedding_text(game) == expected


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            {
                "short_description": " Explore worlds. ",
                "genres": ["Strategy", "Adventure"],
                "categories": ["Single-player", "Co-op"],
            },
            id="complete",
        ),
        pytest.param(
            {"short_description": None, "genres": None, "categories": None},
            id="missing",
        ),
        pytest.param(
            {"short_description": " \t\n", "genres": [], "categories": []},
            id="empty",
        ),
    ],
)
def test_identical_metadata_produces_identical_text(metadata):
    game = Game(steam_app_id=10, name="Example Game", **metadata)
    expected = build_game_embedding_text(game)

    for _ in range(5):
        equivalent_game = Game(steam_app_id=10, name="Example Game", **metadata)
        assert build_game_embedding_text(game) == expected
        assert build_game_embedding_text(equivalent_game) == expected
