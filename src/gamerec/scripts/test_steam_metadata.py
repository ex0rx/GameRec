import asyncio
import json

from gamerec.integrations.steam import (
    fetch_steam_app_details,
    fetch_steam_app_reviews,
)


async def main():
    steam_app_id = 440  # Team Fortress 2

    details = await fetch_steam_app_details(steam_app_id)
    reviews = await fetch_steam_app_reviews(steam_app_id)

    print("=== NORMALISED DETAILS ===")
    print(json.dumps(normalise_game_details(details), indent=2))

    print("\n=== REVIEWS ===")
    print(json.dumps(reviews, indent=2))

def normalise_game_details(data: dict) -> dict:
    return {
        "short_description": data.get("short_description"),
        "genres": [
            genre["description"]
            for genre in data.get("genres", [])
        ],
        "categories": [
            category["description"]
            for category in data.get("categories", [])
        ],
        "developers": data.get("developers", []),
        "publishers": data.get("publishers", []),
        "release_date": data.get("release_date", {}).get("date"),
        "is_free": data.get("is_free"),
        "header_image": data.get("header_image"),

    }
if __name__ == "__main__":
    asyncio.run(main())