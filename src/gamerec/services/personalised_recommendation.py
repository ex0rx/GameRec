"""Small HTTP-facing orchestration of the existing recommendation pipeline."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.models.user import User
from gamerec.services.hybrid_user_recommendation import (
    RankedCandidate,
    build_candidate_affinity_scores,
    build_ranking_features,
    build_user_affinity_profile,
    filter_eligible_candidates,
    get_candidate_ranking_metadata,
    get_global_genre_statistics,
    get_total_max_reviews,
    get_user_affinity_metadata,
    rank_candidates,
)
from gamerec.services.user_game_preferences import UserNotFoundError
from gamerec.services.user_profile import build_user_profile_vector
from gamerec.services.user_recommendation import (
    get_multi_source_recommendation_candidates,
)

# Match the current manual recommendation script; weights remain in rank_candidates.
PROFILE_CANDIDATE_K = 5000
SEED_GAME_COUNT = 10
SEED_CANDIDATE_K = 100
MIN_TOTAL_REVIEWS = 500


async def get_user_recommendations(
    db: AsyncSession, steamid64: str, *, limit: int = 20
) -> list[RankedCandidate]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")

    statement = (
        select(User.steamid64)
        .where(User.steamid64 == steamid64)
        .execution_options(autoflush=False)
    )
    if (await db.execute(statement)).scalar_one_or_none() is None:
        raise UserNotFoundError()

    profile = await build_user_profile_vector(db, steamid64)
    if profile is None:
        return []
    vector, owned_ids = profile

    client = get_qdrant_client()
    try:
        generation = await get_multi_source_recommendation_candidates(
            db=db,
            client=client,
            steamid64=steamid64,
            user_profile_vector=vector,
            owned_ids=owned_ids,
            profile_candidate_k=PROFILE_CANDIDATE_K,
            seed_game_count=SEED_GAME_COUNT,
            seed_candidate_k=SEED_CANDIDATE_K,
        )
        if not generation.candidates:
            return []
        metadata = await get_candidate_ranking_metadata(
            db, [candidate["steam_app_id"] for candidate in generation.candidates]
        )
        eligible = filter_eligible_candidates(
            generation.candidates, metadata, min_total_reviews=MIN_TOTAL_REVIEWS
        )
        if not eligible:
            return []
        eligible_metadata = {
            candidate["steam_app_id"]: metadata[candidate["steam_app_id"]]
            for candidate in eligible
        }
        max_reviews = await get_total_max_reviews(db)
        features = build_ranking_features(
            eligible_metadata, max_reviews, as_of=datetime.now(UTC).date()
        )
        user_metadata = await get_user_affinity_metadata(db, steamid64)
        total_games, genre_counts = await get_global_genre_statistics(db)
        affinity_profile = build_user_affinity_profile(
            user_metadata, total_games=total_games, genre_counts=genre_counts
        )
        affinities = build_candidate_affinity_scores(
            eligible_metadata, affinity_profile
        )
        return rank_candidates(eligible, features, affinities)[:limit]
    finally:
        await client.close()
