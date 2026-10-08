from dataclasses import dataclass
from math import hypot, isfinite, log1p

from qdrant_client import AsyncQdrantClient
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.ml.similarity import cosine_similarity
from gamerec.models.game_embedding import GameEmbedding
from gamerec.models.user_game import UserGame
from gamerec.models.user_game_preference import UserGamePreference
from gamerec.services.user_profile import (
    MIN_PROFILE_NORM,
    PREFERENCE_WEIGHTS,
    UNRATED_WEIGHT,
    UserGameProfile,
    fetch_game_vectors,
)


@dataclass(frozen=True)
class CandidateGenerationResult:
    candidates: list[dict]
    profile_count: int
    seed_ids: list[int]
    seed_candidate_count: int
    merged_count: int
    after_owned_count: int


async def get_user_recommendation_candidates(
    client: AsyncQdrantClient,
    user_profile_vector: list[float],
    candidate_k: int = 1000,
) -> list[dict]:
    """
    Retrieve a list of candidate games for recommendation based on the user's profile vector.

    Args:
        client (AsyncQdrantClient): The Qdrant client instance.
        user_profile_vector (list[float]): The user's profile vector.
        candidate_k (int): The number of candidate games to retrieve.

    Returns:
        list[dict]: A list of candidate games with their details.
    """
    if candidate_k <= 0:
        raise ValueError("candidate_k must be a non-zero positive integer.")

    response = await client.query_points(
        collection_name=settings.qdrant_game_collection,
        query=user_profile_vector,
        limit=candidate_k,
        with_payload=True,
        with_vectors=False,
    )

    return [
        {
            "steam_app_id": int(point.id),
            "name": point.payload.get("name") if point.payload else None,
            "score": point.score,
        }
        for point in response.points
    ]


def filter_owned_games(
    candidates: list[dict],
    user_steam_app_ids: set[int],
) -> list[dict]:
    """
    Filter out games that the user already owns from the list of candidate games.

    Args:
        candidates (list[dict]): The list of candidate games.
        user_steam_app_ids (set[int]): The set of Steam App IDs that the user already owns.

    Returns:
        list[dict]: A filtered list of candidate games that the user does not own.
    """
    return [
        candidate
        for candidate in candidates
        if candidate["steam_app_id"] not in user_steam_app_ids
    ]


def select_representative_seed_games(
    games: list[UserGameProfile],
    seed_game_count: int = 8,
) -> list[UserGameProfile]:
    """Choose positive, embedded library evidence in a stable order."""
    if seed_game_count < 0:
        raise ValueError("seed_game_count must be non-negative")

    eligible = [
        game
        for game in games
        if game.preference != "disliked"
        and ((game.playtime_minutes or 0) > 0 or game.preference == "liked")
    ]
    eligible = [
        game
        for game in eligible
        if len(game.vector) == settings.embeddings_vector_size
        and all(isfinite(value) for value in game.vector)
        and MIN_PROFILE_NORM < hypot(*game.vector) < float("inf")
    ]
    return sorted(
        eligible,
        key=lambda game: (
            -log1p(max(0, game.playtime_minutes or 0))
            * PREFERENCE_WEIGHTS.get(game.preference, UNRATED_WEIGHT),
            -PREFERENCE_WEIGHTS.get(game.preference, UNRATED_WEIGHT),
            game.steam_app_id,
        ),
    )[:seed_game_count]


async def get_representative_seed_games(
    db: AsyncSession,
    steamid64: str,
    seed_game_count: int = 8,
) -> list[UserGameProfile]:
    """Load owned games with current stored embeddings in one database query."""
    if seed_game_count < 0:
        raise ValueError("seed_game_count must be non-negative")
    if seed_game_count == 0:
        return []

    statement = (
        select(
            UserGame.steam_app_id,
            UserGame.playtime_forever_minutes,
            UserGamePreference.preference,
            GameEmbedding.embedding,
        )
        .outerjoin(
            UserGamePreference,
            and_(
                UserGamePreference.steamid64 == UserGame.steamid64,
                UserGamePreference.steam_app_id == UserGame.steam_app_id,
            ),
        )
        .join(
            GameEmbedding,
            and_(
                GameEmbedding.steam_app_id == UserGame.steam_app_id,
                GameEmbedding.model_name == settings.embeddings_model_name,
                GameEmbedding.model_revision == settings.embeddings_model_revision,
            ),
        )
        .where(UserGame.steamid64 == steamid64)
    )
    with db.no_autoflush:
        rows = (await db.execute(statement)).all()

    return select_representative_seed_games(
        [
            UserGameProfile(appid, vector, playtime, preference)
            for appid, playtime, preference, vector in rows
            if vector is not None
        ],
        seed_game_count,
    )


def merge_candidate_ids(
    profile_candidates: list[dict],
    seed_candidates: list[dict],
) -> list[dict]:
    """Keep the first candidate for each Steam app ID."""
    merged = {}
    for candidate in profile_candidates + seed_candidates:
        merged.setdefault(candidate["steam_app_id"], candidate)
    return list(merged.values())


async def get_multi_source_recommendation_candidates(
    db: AsyncSession,
    client: AsyncQdrantClient,
    steamid64: str,
    user_profile_vector: list[float],
    owned_ids: set[int],
    profile_candidate_k: int = 1000,
    seed_game_count: int = 8,
    seed_candidate_k: int = 100,
) -> CandidateGenerationResult:
    """Union profile and seed retrieval, then score every candidate to the profile."""
    if seed_candidate_k <= 0:
        raise ValueError("seed_candidate_k must be positive")

    profile_candidates = await get_user_recommendation_candidates(
        client, user_profile_vector, candidate_k=profile_candidate_k
    )
    seeds = await get_representative_seed_games(db, steamid64, seed_game_count)
    seed_candidates = []
    for seed in seeds:
        seed_candidates.extend(
            await get_user_recommendation_candidates(
                client, seed.vector, candidate_k=seed_candidate_k
            )
        )

    merged = merge_candidate_ids(profile_candidates, seed_candidates)
    unowned = filter_owned_games(merged, owned_ids)
    vectors = (
        await fetch_game_vectors(db, [candidate["steam_app_id"] for candidate in unowned])
        if unowned
        else None
    ) or {}
    candidates = []
    for candidate in unowned:
        vector = vectors.get(candidate["steam_app_id"])
        if vector is None or len(vector) != len(user_profile_vector):
            continue
        if not all(isfinite(value) for value in vector):
            continue
        try:
            score = cosine_similarity(user_profile_vector, vector)
        except ValueError:  # A persisted zero vector cannot be scored.
            continue
        if isfinite(score):
            candidates.append({**candidate, "score": score})

    return CandidateGenerationResult(
        candidates=candidates,
        profile_count=len(profile_candidates),
        seed_ids=[seed.steam_app_id for seed in seeds],
        seed_candidate_count=len(seed_candidates),
        merged_count=len(merged),
        after_owned_count=len(unowned),
    )
