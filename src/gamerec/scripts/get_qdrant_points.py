import asyncio

from gamerec.core.config import settings
from gamerec.db import SessionLocal
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.services.vector_sync import sync_embeddings_to_qdrant


async def main() -> None:
    async with SessionLocal() as db:
        client = get_qdrant_client()

        try:
            response = await sync_embeddings_to_qdrant(
                db=db,
                client=client,
                batch_size=2,
                max_games=5,
            )

            print(response)

            points, _ = await client.scroll(
            collection_name=settings.qdrant_game_collection,
            limit=5,
            with_vectors=True,
            with_payload=True,
        )

            for point in points:
                print("id:", point.id)
                print("vector_size:", len(point.vector))
                print("payload:", point.payload)
                print()
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())