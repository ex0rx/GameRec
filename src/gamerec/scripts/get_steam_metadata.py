import asyncio

import httpx

from gamerec.db import SessionLocal
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.services.steam_ingestion import get_steam_metadata


async def main():
    pacer = RequestPacer(min_interval=1)

    async with (
        SessionLocal() as db,
        httpx.AsyncClient(
            timeout=30,
            event_hooks={"request": [pacer]},
        ) as client,
    ):
        _ = await get_steam_metadata(
            client=client,
            db=db,
            batch_size=16,
            max_games=10000,
            request_delay=1,
            max_concurrent_requests=2,
            max_consecutive_rate_limits=5,
        )


if __name__ == "__main__":
    asyncio.run(main())
