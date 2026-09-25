import asyncio

from gamerec.integrations.qdrant import get_qdrant_client
from gamerec.services.vector_store import find_similar_games


async def main() -> None:
        client = get_qdrant_client()
        try:
            response, target_name = await find_similar_games(
                client=client,
                steam_app_id=550,
                top_k=10,
            )

            print(f"Similar games to Steam App ID 550 ({target_name}):")
            for game in response:
                print(
                    f"Steam App ID: {game['steam_app_id']}, "
                    f"Name: {game['name']}, "
                    f"Score: {game['score']}"
                )
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())