# src/gamerec/integrations/steam.py

import json
from datetime import date, datetime

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
    params = {
            "key": settings.steam_api_key,
            "input_json": json.dumps(input_json),
        }
    
    if if_modified_since is not None:
        input_json["if_modified_since"] = if_modified_since

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

    response = await client.get(
        STEAM_APP_DETAILS_API_URL,
        params=params,
    )

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

    response = await client.get(
        STEAM_APP_REVIEW_API_URL.format(steam_app_id=steam_app_id),
        params=params,
    )

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