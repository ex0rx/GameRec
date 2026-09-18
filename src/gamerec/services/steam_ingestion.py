from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.integrations.steam import fetch_games
from gamerec.models.game import Game


async def ingest_steam_games(
    db: AsyncSession,
    last_appid: int = 0,
    max_results: int = 100,
) -> int:
    steam_games = await fetch_games(
        last_appid=last_appid,
        max_results=max_results,
    )

    games = [
        {
            "steam_app_id": game["appid"],
            "name": game["name"],
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
        },
    )

    await db.execute(statement)
    await db.commit()

    return len(games)