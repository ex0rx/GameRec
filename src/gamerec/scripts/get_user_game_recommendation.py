import asyncio

from gamerec.core.config import settings
from gamerec.db import SessionLocal
from gamerec.services.user_profile import build_user_profile_vector
from gamerec.services.user_recommendation import (
    filter_owned_games,
    get_user_recommendation_candidates,
)
from gamerec.services.vector_store import get_qdrant_client

STEAMID64 = settings.steamid64_test
TOP_K = 10
CANDIDATE_K = 50


async def main() -> None:
    async with SessionLocal() as db:
        client = get_qdrant_client()

        try:
            profile_result = await build_user_profile_vector(
                db=db,
                steamid64=STEAMID64,
            )

            if profile_result is None:
                print("Could not build a user profile.")
                return

            user_profile_vector, owned_ids = profile_result

            candidates = await get_user_recommendation_candidates(
                client=client,
                user_profile_vector=user_profile_vector,
                candidate_k=CANDIDATE_K,
            )

            recommendations = filter_owned_games(
                candidates,
                owned_ids,
            )[:TOP_K]

            if not recommendations:
                print("No recommendations found.")
                return

            print(f"Top {len(recommendations)} recommendations:")
            print()

            for rank, game in enumerate(recommendations, start=1):
                print(
                    f"{rank:2}. "
                    f"{game['name'] or 'Unknown'} "
                    f"(Steam App ID: {game['steam_app_id']}) "
                    f"Similarity: {game['score']:.4f}"
                )

        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())