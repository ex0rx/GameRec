# src/gamerec/integrations/steam.py

import asyncio
import json
from datetime import date, datetime
from typing import Literal

import httpx

from gamerec.core.config import settings

STEAM_API_URL = (
    "https://api.steampowered.com/"
    "IStoreService/GetAppList/v1/"
)

STEAM_APP_DETAILS_API_URL = (
    "https://store.steampowered.com/api/appdetails"
)

STEAM_APP_REVIEW_API_URL = (
    "https://store.steampowered.com/appreviews/{steam_app_id}"
)

STEAM_OWNED_GAMES_API_URL = (
    "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
)

async def fetch_steam_games(
    client: httpx.AsyncClient,
    last_appid: int = 0,
    max_results: int = 100,
    if_modified_since: int | None = None,
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
    if if_modified_since is not None:
        input_json["if_modified_since"] = if_modified_since

    params = {
                "key": settings.steam_api_key,
                "input_json": json.dumps(input_json),
            }

    response = await client.get(
        STEAM_API_URL,
        params=params,
    )

    response.raise_for_status()

    data = response.json()

    return data["response"].get("apps", [])

async def fetch_steam_app_details(
        client: httpx.AsyncClient,
        steam_app_id: int
        ) -> dict | None:
    params = {
        "appids": steam_app_id,
        "l": "english",
    }
    url = STEAM_APP_DETAILS_API_URL

    response = await get_with_retry(client, url=url, params=params)

    response.raise_for_status()
    
    data = response.json()

    app_data = data.get(str(steam_app_id), {})
    if not app_data.get("success"):
        return None

    return app_data.get("data")

async def fetch_steam_app_reviews(
    client: httpx.AsyncClient,
    steam_app_id: int
) -> dict | None:
    params = {
        "json": 1,
        "filter": "all",
        "language": "all",
        "purchase_type": "all",
        "review_type": "all",
        "num_per_page": 1,
    }
    url = STEAM_APP_REVIEW_API_URL.format(steam_app_id=steam_app_id)

    response = await get_with_retry(client, url=url, params=params)

    response.raise_for_status()

    data = response.json()
    if data.get("success") != 1:
        return None

    return data.get("query_summary")

def parse_steam_release_date(value: str | None) -> date | None:
    if not value:
        return None

    formats = [
        "%d %b, %Y",
        "%b %d, %Y",
    ]

    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date() # noqa: DTZ007
        except ValueError:
            continue

    return None

def normalise_game_details(data: dict) -> dict:
    release = data.get("release_date") or {}

    if release.get("coming_soon"):
        release_date = None
    else:
        release_date = parse_steam_release_date(
            release.get("date")
        )

    return {
        "short_description": data.get("short_description"),
        "genres": [
            genre["description"]
            for genre in data.get("genres", [])
            if "description" in genre
        ],
        "categories": [
            category["description"]
            for category in data.get("categories", [])
            if "description" in category
        ],
        "developers": data.get("developers", []),
        "publishers": data.get("publishers", []),
        "release_date": release_date,
        "is_free": data.get("is_free"),
        "header_image": data.get("header_image"),
    }

async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    max_attempts: int = 3,
) -> httpx.Response:
    for attempt in range(max_attempts):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code

            if status != 429 and status < 500:
                raise

            if attempt == max_attempts - 1:
                raise

            delay = 2**attempt

            if status == 429:
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, int(retry_after))

        except httpx.RequestError:
            if attempt == max_attempts - 1:
                raise

            delay = 2**attempt

        await asyncio.sleep(delay)

    raise RuntimeError("Retry loop ended unexpectedly")

async def fetch_steam_owned_games(
    client: httpx.AsyncClient,
    steamid64: str,
    include_appinfo: bool = True,
    include_played_free_games: bool = True,
) -> dict:
    url = STEAM_OWNED_GAMES_API_URL
    params = {
        "key": settings.steam_api_key,
        "steamid": steamid64,
        "include_appinfo": int(include_appinfo),
        "include_played_free_games": int(include_played_free_games),
    }

    response = await get_with_retry(client, url, params)
    payload = response.json()

    if not isinstance(payload, dict) or "response" not in payload:
        raise ValueError("Expected a JSON object from Steam")

    return payload

def normalise_owned_games(
    payload: dict,
) -> tuple[Literal["available", "unavailable"], int | None, list[dict]]:
    response = payload.get("response")

    if not isinstance(response, dict):
        raise ValueError("Expected 'response' to be a dictionary")

    if response == {}:
        return "unavailable", None, []

    reported_game_count = response.get("game_count")

    if (
        isinstance(reported_game_count, bool)
        or not isinstance(reported_game_count, int)
        or reported_game_count < 0
    ):
        raise ValueError("Expected 'game_count' to be a non-negative integer")

    if "games" not in response:
        if reported_game_count == 0:
            return "available", 0, []

        raise ValueError("Expected 'games' when 'game_count' is positive")

    games = response["games"]

    if not isinstance(games, list):
        raise ValueError("Expected 'games' to be a list")

    if len(games) != reported_game_count:
        raise ValueError("'game_count' does not match the number of games")

    for game in games:
        if not isinstance(game, dict):
            raise ValueError("Expected each game to be a dictionary")

        appid = game.get("appid")

        if isinstance(appid, bool) or not isinstance(appid, int) or appid <= 0:
            raise ValueError("Expected each game to have a positive integer 'appid'")

    return "available", reported_game_count, games

   

    