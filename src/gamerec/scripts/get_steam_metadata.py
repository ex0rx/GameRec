import asyncio

import httpx

from gamerec.db import SessionLocal
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.services.steam_ingestion import get_steam_metadata


async def main():
    pacer = RequestPacer(min_interval=0.5)

    async with SessionLocal() as db, httpx.AsyncClient(
        timeout=30,
        event_hooks={"request": [pacer]},
    ) as client:
        count = await get_steam_metadata(
            client=client,
            db=db,
            batch_size=10,
            max_games=10,
            request_delay=0,
            max_concurrent_requests=2,
        )

    print(f"Attempted {count} games")


if __name__ == "__main__":
    asyncio.run(main())