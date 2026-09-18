import asyncio

from gamerec.db import SessionLocal
from gamerec.services.steam_ingestion import ingest_steam_games


async def main() -> None:
    async with SessionLocal() as db:
        count = await ingest_steam_games(
            db,
            max_results=5,
        )

    print(f"Ingested {count} Steam games")


if __name__ == "__main__":
    asyncio.run(main())