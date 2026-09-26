import asyncio

import httpx

from gamerec.core.config import settings
from gamerec.db import SessionLocal
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.integrations.steam import fetch_steam_owned_games, normalise_owned_games
from gamerec.services.steam_ingestion import get_steam_metadata
from gamerec.services.steam_library import save_steam_library


async def main():
    steamid64 = settings.steamid64_test
    if not steamid64:
        raise ValueError("Configure STEAMID64_TEST before running this script")

    pacer = RequestPacer(min_interval=0.5)

    async with httpx.AsyncClient(
        timeout=30,
        event_hooks={"request": [pacer]},
    ) as client:
        payload = await fetch_steam_owned_games(
            client=client,
            steamid64=steamid64,
            include_appinfo=True,
            include_played_free_games=True,
        )

        status, reported_game_count, normalised_games = normalise_owned_games(payload)

        print(f"Library status: {status}")
        print(f"Reported game count: {reported_game_count}")
        print(f"Parsed entries: {len(normalised_games)}")
        print(f"First 5 entries: {normalised_games[:5]}")

        if status != "available":
            print("Library unavailable, skipping database save")
            return

        async with SessionLocal() as db:
            total_processed = await save_steam_library(
                db=db,
                steamid64=steamid64,
                normalised_games=normalised_games,
            )

        print(f"Total processed games: {total_processed}")

        library_app_ids = [game["steam_app_id"] for game in normalised_games]

        async with SessionLocal() as metadata_db:
            attempted = await get_steam_metadata(
                client=client,
                db=metadata_db,
                batch_size=10,
                max_games=100,
                request_delay=0.25,
                max_concurrent_requests=2,
                max_consecutive_rate_limits=3,
                steam_app_ids=library_app_ids,
            )
        print(f"Attempted to fetch metadata for {attempted} games")


if __name__ == "__main__":
    asyncio.run(main())
