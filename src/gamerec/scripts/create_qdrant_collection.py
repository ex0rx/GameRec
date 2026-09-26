import asyncio

from gamerec.core.config import settings
from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.services.vector_store import ensure_game_collection


async def main() -> None:
    client = get_qdrant_client()

    try:
        await ensure_game_collection(
            client,
            collection_name=settings.qdrant_game_collection,
        )
        print(f"Created {settings.qdrant_game_collection}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
