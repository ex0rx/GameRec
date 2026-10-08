import asyncio
import time
from datetime import UTC, datetime

from gamerec.core.config import settings
from gamerec.db import SessionLocal
from gamerec.services.hybrid_user_recommendation import (
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
from gamerec.services.user_profile import build_user_profile_vector
from gamerec.services.user_recommendation import (
    get_multi_source_recommendation_candidates,
)
from gamerec.services.vector_store import get_qdrant_client

STEAMID64 = settings.steamid64_test
TOP_K = 20
PROFILE_CANDIDATE_K = 5000
SEED_GAME_COUNT = 10
SEED_CANDIDATE_K = 100
MIN_TOTAL_REVIEWS = 500


async def main() -> None:
    started_at = time.perf_counter()
    async with SessionLocal() as db:
        client = get_qdrant_client()

        try:
            user_affinity_metadata = await get_user_affinity_metadata(
                db=db,
                steamid64=STEAMID64,
            )

            total_games, genre_counts = await get_global_genre_statistics(db)
            user_affinity_profile = build_user_affinity_profile(
                user_affinity_metadata,
                total_games=total_games,
                genre_counts=genre_counts,
            )
            uncapped_affinity_profile = build_user_affinity_profile(
                user_affinity_metadata,
                total_games=total_games,
                genre_counts=genre_counts,
                playtime_percentile=None,
            )

            print("Strongest affinity scores (uncapped -> p90 capped):")
            for label, uncapped, capped in (
                (
                    "Genres",
                    uncapped_affinity_profile.genres_score,
                    user_affinity_profile.genres_score,
                ),
                (
                    "Categories",
                    uncapped_affinity_profile.categories_score,
                    user_affinity_profile.categories_score,
                ),
            ):
                strongest = sorted(
                    uncapped.keys() | capped.keys(),
                    key=lambda name: (
                        -max(abs(uncapped.get(name, 0)), abs(capped.get(name, 0))),
                        name,
                    ),
                )[:5]
                print(f"{label}:")
                for name in strongest:
                    before = uncapped.get(name, 0.0)
                    after = capped.get(name, 0.0)
                    print(f"  {name}: {before:.3f} -> {after:.3f} ({after - before:+.3f})")

            profile_result = await build_user_profile_vector(
                db=db,
                steamid64=STEAMID64,
            )

            if profile_result is None:
                print("Could not build a user profile.")
                return

            user_profile_vector, owned_ids = profile_result

            generation = await get_multi_source_recommendation_candidates(
                db=db,
                client=client,
                steamid64=STEAMID64,
                user_profile_vector=user_profile_vector,
                owned_ids=owned_ids,
                profile_candidate_k=PROFILE_CANDIDATE_K,
                seed_game_count=SEED_GAME_COUNT,
                seed_candidate_k=SEED_CANDIDATE_K,
            )
            candidates = generation.candidates
            print(f"Profile candidates: {generation.profile_count}")
            print(f"Seeds selected: {len(generation.seed_ids)} {generation.seed_ids}")
            print(f"Seed candidates before dedupe: {generation.seed_candidate_count}")
            print(f"Merged unique candidates: {generation.merged_count}")
            print(f"Candidates after owned filter: {generation.after_owned_count}")
            print(f"Candidates with embeddings: {len(candidates)}")

            candidate_steam_app_ids = [
                candidate["steam_app_id"] for candidate in candidates
            ]

            candidate_metadata = await get_candidate_ranking_metadata(
                db=db,
                steam_app_ids=candidate_steam_app_ids,
            )

            candidates = filter_eligible_candidates(
                candidates=candidates,
                metadata=candidate_metadata,
                min_total_reviews=MIN_TOTAL_REVIEWS,
            )

            print(f"Candidates after eligibility filter: {len(candidates)}")

            max_total_reviews = await get_total_max_reviews(db)

            candidate_features = build_ranking_features(
                metadata=candidate_metadata,
                max_total_reviews=max_total_reviews,
                as_of=datetime.now(UTC).date(),
            )

            candidate_affinity_scores = build_candidate_affinity_scores(
                metadata=candidate_metadata,
                user_affinity_profile=user_affinity_profile,
            )

            ranked_candidates = rank_candidates(
                candidates=candidates,
                features=candidate_features,
                user_affinity_scores=candidate_affinity_scores,
            )

            recommendations = ranked_candidates[:TOP_K]

            print(f"Top {TOP_K} recommendations:")
            for rank, candidate in enumerate(recommendations, start=1):
                affinity = candidate_affinity_scores[candidate.steam_app_id]
                print(
                    f"{rank}. {candidate.name} (Steam App ID: {candidate.steam_app_id}) - "
                    f"Hybrid Score: {candidate.hybrid_score:.4f}, "
                    f"Similarity Score: {candidate.similarity_score:.4f}, "
                    f"Popularity Score: {candidate.popularity_score:.4f}, "
                    f"Review Quality: {candidate.review_quality:.4f}, "
                    f"Recency Score: {candidate.recency_score:.4f}, "
                    f"Genre Affinity: {affinity.genres_affinity:.4f}, "
                    f"Category Affinity: {affinity.categories_affinity:.4f}, "
                    f"Combined Affinity: {affinity.affinity_score:.4f}"
                )

            print(f"Total time: {time.perf_counter() - started_at:.2f} seconds")

        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
