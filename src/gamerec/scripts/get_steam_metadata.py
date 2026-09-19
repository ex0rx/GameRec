import asyncio

import httpx

from gamerec.db import SessionLocal
from gamerec.services.steam_ingestion import get_steam_metadata


async def main():
    async with SessionLocal() as db:
        count = await get_steam_metadata(
            client=httpx.AsyncClient(),
            db=db,
            batch_size=10,
            max_games=50,
            request_delay=0.5,
        )

    print(f"Processed {count} games")


if __name__ == "__main__":
    asyncio.run(main())