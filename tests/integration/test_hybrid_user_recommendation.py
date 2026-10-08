"""Genre document frequency against an isolated PostgreSQL catalogue."""

import pytest
from sqlalchemy import null

from gamerec.models.game import Game
from gamerec.services.hybrid_user_recommendation import get_global_genre_statistics

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_genre_counts_use_distinct_games_with_usable_available_metadata(
    pg_sessions,
):
    fixtures = [
        (True, ["Action", "Action", "RPG", "Free To Play"]),
        (True, ["Action", "Early Access"]),
        (True, ["Free To Play", "Early Access"]),
        (True, ["Strategy", "", "   ", None, 42]),
        (True, []),
        (True, None),  # JSON null
        (True, null()),  # SQL NULL
        (True, {"genre": "Action"}),
        (True, "Action"),
        (True, [None, "", "   ", 42, {"genre": "Action"}]),
        (False, ["Excluded"]),
        (None, ["Unchecked"]),
    ]
    async with pg_sessions() as db:
        db.add_all(
            Game(
                steam_app_id=appid,
                name=f"Synthetic game {appid}",
                metadata_available=available,
                genres=genres,
            )
            for appid, (available, genres) in enumerate(fixtures, start=1)
        )
        await db.commit()
        assert await get_global_genre_statistics(db) == (
            3,
            {"Action": 2, "RPG": 1, "Strategy": 1},
        )


async def test_empty_genre_corpus_returns_zero_counts(pg_sessions):
    async with pg_sessions() as db:
        assert await get_global_genre_statistics(db) == (0, {})
        db.add(
            Game(steam_app_id=1, name="No genres", metadata_available=True, genres=[])
        )
        db.add(
            Game(
                steam_app_id=2,
                name="No gameplay genres",
                metadata_available=True,
                genres=["Free To Play", "Early Access"],
            )
        )
        await db.commit()
        assert await get_global_genre_statistics(db) == (0, {})
