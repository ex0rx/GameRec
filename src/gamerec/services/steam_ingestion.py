from datetime import datetime, UTC

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.integrations.steam import fetch_games
from gamerec.models.game import Game


async def upsert_steam_games(
    db: AsyncSession,
    steam_games: list[dict],
) -> int:
    games = [
        {
            "steam_app_id": game["appid"],
            "name": game["name"],
            "last_modified": (
                datetime.fromtimestamp(game["last_modified"], tz=UTC)
                if game.get("last_modified") is not None
                else None
            ),
            "price_change_number": game.get("price_change_number", 0),
        }
        for game in steam_games
        if game.get("appid") and game.get("name")
    ]

    if not games:
        return 0

    statement = insert(Game).values(games)

    statement = statement.on_conflict_do_update(
        index_elements=[Game.steam_app_id],
        set_={
            "name": statement.excluded.name,
            "last_modified": statement.excluded.last_modified,
            "price_change_number": statement.excluded.price_change_number,
        },
    )

    await db.execute(statement)
    await db.commit()

    return len(games)


async def ingest_steam_catalogue(
    db: AsyncSession,
    page_size: int = 1000,
    max_pages: int | None = None,
    if_modified_since: int | None = None,
) -> int:
    last_appid = 0
    total_ingested = 0
    page = 0

    while True:
        if max_pages is not None and page >= max_pages: # define number of pages to fetch, if max_pages is None, fetch all pages
            break

        steam_games = await fetch_games( # fetch page_size number of games greater than last_appid
            last_appid=last_appid,
            max_results=page_size,
            if_modified_since=if_modified_since,
        )

        if not steam_games:
            break

        count = await upsert_steam_games( # update or insert the fetched games into the database
            db=db,
            steam_games=steam_games,
        )

        total_ingested += count
        page += 1

        new_last_appid = steam_games[-1]["appid"]

        # Safety check so we can never accidentally loop forever
        if new_last_appid <= last_appid:
            raise RuntimeError(
                "Steam pagination did not advance last_appid"
            )

        last_appid = new_last_appid

        print(
            f"Page {page}: ingested {count} games "
            f"(last_appid={last_appid})"
        )

        # Last partial page means we've reached the end
        if len(steam_games) < page_size:
            break

    return total_ingested