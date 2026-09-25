import asyncio

from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.services.vector_store import find_similar_games


async def main() -> None:
        client = get_qdrant_client()
        try:
            response = await find_similar_games(
                client=client,
                steam_app_id=550,
                top_k=5,
            )

            print(response)

        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())