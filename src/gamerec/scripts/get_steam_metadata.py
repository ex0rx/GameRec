import asyncio
from itertools import batched

import httpx

from gamerec.db import SessionLocal
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.services.steam_ingestion import (
    get_random_unsynced_game_ids,
    get_steam_metadata,
)


async def main():
    pacer = RequestPacer(min_interval=1)

    async with (
        SessionLocal() as db,
        httpx.AsyncClient(
            timeout=30,
            event_hooks={"request": [pacer]},
        ) as client,
    ):

        steam_app_ids = await get_random_unsynced_game_ids(
            db,
            limit=30_000,
        )

        for app_id_batch in batched(steam_app_ids, 1000):
            await get_steam_metadata(
                db=db,
                client=client,
                steam_app_ids=list(app_id_batch),
                batch_size=25,
                max_games=None,
                request_delay=0.5,
                max_concurrent_requests=2,
                max_consecutive_rate_limits=3,
            )


if __name__ == "__main__":
    asyncio.run(main())
