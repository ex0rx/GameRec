import asyncio

import httpx

from gamerec.core.config import settings
from gamerec.integrations.request_pacer import RequestPacer
from gamerec.integrations.steam import fetch_steam_owned_games, normalise_owned_games


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

    status, reported_game_count, games = normalise_owned_games(payload)

    print(f"Library status: {status}")
    print(f"Reported game count: {reported_game_count}")
    print(f"Parsed entries: {len(games)}")


if __name__ == "__main__":
    asyncio.run(main())