# src/gamerec/integrations/steam.py

import json

import httpx

from gamerec.core.config import settings


STEAM_API_URL = (
    "https://api.steampowered.com/"
    "IStoreService/GetAppList/v1/"
)


async def fetch_games(
    last_appid: int = 0,
    max_results: int = 100,
) -> list[dict]:
    input_json = {
        "include_games": True,
        "include_dlc": False,
        "include_software": False,
        "include_videos": False,
        "include_hardware": False,
        "last_appid": last_appid,
        "max_results": max_results,
    }

    params = {
        "key": settings.steam_api_key,
        "input_json": json.dumps(input_json),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            STEAM_API_URL,
            params=params,
        )

        response.raise_for_status()

        data = response.json()

    return data["response"].get("apps", [])