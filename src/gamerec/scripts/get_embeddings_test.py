"""Run the bounded, read-only embedding verification job."""

import asyncio

from gamerec.db import SessionLocal, engine
from gamerec.services.game_embeddings import generate_game_embeddings

STEAM_APP_IDS = [105600, 730]
EXPECTED_DIMENSIONS = 384


async def main() -> None:
    try:
        async with SessionLocal() as db:
            embeddings = await generate_game_embeddings(
                db=db, steam_app_ids=STEAM_APP_IDS
            )

        for steam_app_id in STEAM_APP_IDS:
            vector = embeddings.get(steam_app_id)
            if vector is None:
                print(
                    f"Steam App ID: {steam_app_id}: no embedding returned "
                    "(game missing from database or no usable embedding text)."
                )
                continue

            dimensions = len(vector)
            print(f"Steam App ID: {steam_app_id}, Embedding dimensions: {dimensions}")
            if dimensions != EXPECTED_DIMENSIONS:
                raise ValueError(
                    f"Expected {EXPECTED_DIMENSIONS} dimensions for {steam_app_id}, "
                    f"got {dimensions}"
                )

        print(f"Generated {len(embeddings)}/{len(STEAM_APP_IDS)} requested embeddings.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
