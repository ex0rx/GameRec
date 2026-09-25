import asyncio

from sentence_transformers import SentenceTransformer

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.services.game_embeddings import process_game_embeddings


async def main() -> None:
    try:
        model = SentenceTransformer(
            settings.embeddings_model_name,
            revision=settings.embeddings_model_revision,
        )

        async with SessionLocal() as db:
            stats = await process_game_embeddings(
                db=db,
                model=model,
                batch_size=64,
                max_games=None,
            )

        print(stats)

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
