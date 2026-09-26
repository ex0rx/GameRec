from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.models.game import Game
from gamerec.models.user import User
from gamerec.models.user_game import UserGame


async def upsert_user_game_library(
    db: AsyncSession,
    steamid64: str,
    normalised_games: list[dict],
) -> int:
    user_statement = (
        insert(User)
        .values(steamid64=steamid64)
        .on_conflict_do_nothing(index_elements=[User.steamid64])
    )
    await db.execute(user_statement)

    if not normalised_games:
        return 0

    app_ids = [game["steam_app_id"] for game in normalised_games]

    if len(app_ids) != len(set(app_ids)):
        raise ValueError("Duplicate app IDs found in normalised_games")

    result = await db.scalars(
        select(Game.steam_app_id).where(Game.steam_app_id.in_(app_ids))
    )
    existing_app_ids = set(result.all())

    missing_games = []

    for game in normalised_games:
        if game["steam_app_id"] in existing_app_ids:
            continue

        name = game.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Expected 'name' to be a non-empty string for app ID {game['steam_app_id']}"
            )

        missing_games.append(
            {
                "steam_app_id": game["steam_app_id"],
                "name": name.strip(),
            }
        )

    if missing_games:
        game_statement = (
            insert(Game)
            .values(missing_games)
            .on_conflict_do_nothing(index_elements=[Game.steam_app_id])
        )
        await db.execute(game_statement)

    user_game_rows = [
        {
            "steamid64": steamid64,
            "steam_app_id": game["steam_app_id"],
            "playtime_forever_minutes": game["playtime_forever_minutes"],
            "playtime_2weeks_minutes": game["playtime_2weeks_minutes"],
        }
        for game in normalised_games
    ]

    statement = insert(UserGame).values(user_game_rows)

    statement = statement.on_conflict_do_update(
        index_elements=[UserGame.steamid64, UserGame.steam_app_id],
        set_={
            "playtime_forever_minutes": statement.excluded.playtime_forever_minutes,
            "playtime_2weeks_minutes": statement.excluded.playtime_2weeks_minutes,
        },
    )

    await db.execute(statement)

    return len(user_game_rows)


async def save_steam_library(
    db: AsyncSession,
    steamid64: str,
    normalised_games: list[dict],
) -> int:
    async with db.begin():
        processed = await upsert_user_game_library(
            db=db, steamid64=steamid64, normalised_games=normalised_games
        )

        await db.execute(
            update(User)
            .where(User.steamid64 == steamid64)
            .values(library_last_synced_at=datetime.now(tz=UTC))
        )
    return processed
