import asyncio

from gamerec.db import SessionLocal, engine
from gamerec.ml.embedding_model import load_embedding_model
from gamerec.services.game_embeddings import process_game_embeddings


async def main() -> None:
    try:
        model = load_embedding_model()

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
