
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.models.game import Game
from gamerec.models.user import User
from gamerec.models.user_game import UserGame


async def get_user_library(
    db: AsyncSession,
    steamid64: str,
    limit: int = 50,
    offset: int = 0,
) -> dict | None: 
    query_user = select(User).where(User.steamid64 == steamid64)
    user_result = await db.execute(query_user)
    user = user_result.scalar_one_or_none()

    if user is None:
        return None 

    query_user_games = (select(func.count())
                        .select_from(UserGame)
                        .where(UserGame.steamid64 == steamid64)
                        )
    user_games_count_result = await db.execute(query_user_games)
    user_games_count = user_games_count_result.scalar_one()

    query_user_library = (select(UserGame, Game)
                          .join(Game, UserGame.steam_app_id == Game.steam_app_id)
                          .where(UserGame.steamid64 == steamid64)
                          .order_by(UserGame.steam_app_id)
                          .offset(offset)
                          .limit(limit)
                        )
    
    user_library_result = await db.execute(query_user_library)
    user_library = user_library_result.all()

    games = []

    for user_game, game in user_library:
        games.append({
            "steam_app_id": game.steam_app_id,
            "name": game.name,
            "playtime_forever_minutes": user_game.playtime_forever_minutes,
            "playtime_2weeks_minutes": user_game.playtime_2weeks_minutes,
        })

    return {
        "steamid64": steamid64,
        "library_last_synced_at": user.library_last_synced_at,
        "total_games": user_games_count,
        "limit": limit,
        "offset": offset,
        "games": games,
    }