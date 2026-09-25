import asyncio

from sqlalchemy import select

from gamerec.core.config import settings
from gamerec.db import SessionLocal, engine
from gamerec.services.game_similarity import find_similar_games
from gamerec.models.game import Game

async def main() -> None:
    try:
        async with SessionLocal() as db:
            steam_app_id = 550  
            results = await find_similar_games(
                db=db,
                steam_app_id=steam_app_id,  # Replace with a valid Steam App ID
                top_k=10,
            )
            app_ids = [app_id for app_id, _ in results]

            statement = select(Game.steam_app_id, Game.name).where(
                Game.steam_app_id.in_(app_ids)
            )

            rows = (await db.execute(statement)).all()

            game_names = {
                app_id: name
                for app_id, name in rows
            }

            for rank, (app_id, similarity) in enumerate(results, start=1):
                print(
                    f"{rank:2}. "
                    f"{game_names.get(app_id, 'Unknown game'):<40} "
                    f"Similarity: {similarity:.4f}"
                )

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())