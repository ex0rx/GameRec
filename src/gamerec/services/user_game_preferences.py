from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.models.game import Game
from gamerec.models.user import User
from gamerec.models.user_game_preference import UserGamePreference


class UserNotFoundError(Exception):
    pass

class GameNotFoundError(Exception):
    pass

async def upsert_user_game_preference(
    db: AsyncSession,
    steamid64: str,
    steam_app_id: int,
    preference: str,
) -> UserGamePreference:
    query_user = select(User).where(User.steamid64 == steamid64)
    user_result = await db.execute(query_user)
    user = user_result.scalar_one_or_none()

    if user is None:
        raise UserNotFoundError()

    query_game = select(Game).where(Game.steam_app_id == steam_app_id)
    game_result = await db.execute(query_game)
    game = game_result.scalar_one_or_none()

    if not game:
        raise GameNotFoundError()

    preference_statement = (
        insert(UserGamePreference)
        .values(
            steamid64=steamid64,
            steam_app_id=steam_app_id,
            preference=preference,
        )
        .on_conflict_do_update(
            index_elements=[UserGamePreference.steamid64, UserGamePreference.steam_app_id],
            set_={
                "preference": preference,
                "preference_last_updated_at": func.now(),
            },
        )
        .returning(UserGamePreference)
    )

    result = await db.execute(preference_statement)
    return result.scalar_one_or_none()    

async def get_user_game_preference(
    db: AsyncSession,
    steamid64: str,
    offset: int = 0,
    limit: int = 50,
) -> list[UserGamePreference]:
    query_user = select(User).where(User.steamid64 == steamid64)
    user_result = await db.execute(query_user)
    user = user_result.scalar_one_or_none()

    if user is None:
        raise UserNotFoundError()

    preference_statement = (
        select(UserGamePreference)
        .where(UserGamePreference.steamid64 == steamid64)
        .order_by(UserGamePreference.steam_app_id)
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(preference_statement)
    return result.scalars().all()

async def delete_user_game_preference(
    db: AsyncSession,
    steamid64: str,
    steam_app_id: int,
) -> None:
    query_user = select(User).where(User.steamid64 == steamid64)
    user_result = await db.execute(query_user)
    user = user_result.scalar_one_or_none()

    if user is None:
        raise UserNotFoundError()

    query_game = select(Game).where(Game.steam_app_id == steam_app_id)
    game_result = await db.execute(query_game)
    game = game_result.scalar_one_or_none()

    if not game:
        raise GameNotFoundError()

    delete_statement = (
        delete(UserGamePreference)
        .where(
            UserGamePreference.steamid64 == steamid64,
            UserGamePreference.steam_app_id == steam_app_id,
        )
    )
    await db.execute(delete_statement)

        

   