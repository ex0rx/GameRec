from dataclasses import dataclass
from math import fsum, hypot, isfinite, log1p

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.models.game_embedding import GameEmbedding
from gamerec.models.user_game import UserGame
from gamerec.models.user_game_preference import UserGamePreference

MIN_PLAYED_GAMES = 3
MIN_PLAYTIME_MINUTES = 300
PREFERENCE_WEIGHTS = {"liked": 1.0, "neutral": 0.4, "disliked": -0.6}
UNRATED_WEIGHT = 0.5
MIN_PROFILE_NORM = 1e-12

@dataclass(frozen=True)
class UserGameProfile:
    steam_app_id: int
    vector: list[float]
    playtime_minutes: int | None
    preference: str | None

def construct_profile_vector(
    games: list[UserGameProfile],
    *,
    dimensions: int = settings.embeddings_vector_size,
) -> list[float] | None:
    
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    
    valid_games = [
        game
        for game in games
        if len(game.vector) == dimensions
        and all(isfinite(value) for value in game.vector)
        and MIN_PROFILE_NORM < hypot(*game.vector) < float("inf")
    ]

    if not valid_games:
        return None
    
    sufficient_playtime = (
        sum(
            (game.playtime_minutes or 0) >= MIN_PLAYTIME_MINUTES
            for game in valid_games
        )
        >= MIN_PLAYED_GAMES
    )

    has_preferences = any(
        game.preference in PREFERENCE_WEIGHTS
        for game in valid_games
    )

    weights = {
        game.steam_app_id: (
            PREFERENCE_WEIGHTS.get(
                game.preference,
                UNRATED_WEIGHT,
            )
            if sufficient_playtime or has_preferences
            else 1.0
        )
        for game in valid_games
    }

    if sufficient_playtime:
        transformed = {
            game.steam_app_id: log1p(
                max(0, game.playtime_minutes or 0)
            )
            for game in valid_games
        }

        total = fsum(transformed.values())

        weights = {
            game.steam_app_id: (
                weights[game.steam_app_id]
                * transformed[game.steam_app_id]
                / total
            )
            for game in valid_games
        }

    if not any(weight > 0 for weight in weights.values()):
        return None

    profile = [
        fsum(
            weights[game.steam_app_id] * game.vector[axis]
            for game in valid_games
        )
        for axis in range(dimensions)
    ]

    norm = hypot(*profile)

    if not isfinite(norm) or norm <= MIN_PROFILE_NORM:
        return None

    return [value / norm for value in profile]


async def build_user_profile_vector(
    db: AsyncSession,
    steamid64: str,
) -> tuple[list[float], set[int]] | None:
    
    statement = (
        select(
            UserGame.steam_app_id,
            UserGame.playtime_forever_minutes,
            UserGamePreference.preference,
        )
        .outerjoin(
            UserGamePreference,
            and_(
                UserGamePreference.steamid64 == UserGame.steamid64,
                UserGamePreference.steam_app_id == UserGame.steam_app_id,
            ),
        )
        .where(UserGame.steamid64 == steamid64)
        .order_by(UserGame.steam_app_id)
    )
    with db.no_autoflush:
        rows = (await db.execute(statement)).all()

    if not rows:
        return None
    
    owned_ids = {appid for appid, _, _ in rows} # rows have appid, playtime, preference

    vectors = await fetch_game_vectors(db, sorted(owned_ids)) # get embeddings for owned games

    if not vectors:
        return None

    games = [
        UserGameProfile(
            steam_app_id=appid,
            vector=vectors[appid],
            playtime_minutes=playtime,
            preference=preference,
        )
        for appid, playtime, preference in rows
        if appid in vectors
    ]

    profile = construct_profile_vector(games, dimensions=settings.embeddings_vector_size)

    if profile is None:
        return None

    return profile, owned_ids


async def fetch_game_vectors(
    db: AsyncSession,
    steam_app_ids: list[int],
) -> dict[int, list[float]] | None:
    """Batch-read persisted vectors for the configured model and revision."""

    if not steam_app_ids:
        return None
    
    statement = select(GameEmbedding.steam_app_id, GameEmbedding.embedding).where(
        GameEmbedding.steam_app_id.in_(steam_app_ids),
        GameEmbedding.model_name == settings.embeddings_model_name,
        GameEmbedding.model_revision == settings.embeddings_model_revision,
    )

    with db.no_autoflush:
        rows = (await db.execute(statement)).all()

    return {
        steam_app_id: embedding for steam_app_id, embedding in rows if embedding is not None
    }
