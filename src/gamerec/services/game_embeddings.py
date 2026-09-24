from sentence_transformers import SentenceTransformer, util

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from gamerec.models.game import Game
from gamerec.ml.game_text import build_game_embedding_text

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

async def generate_game_embeddings(
    db: AsyncSession,
    steam_app_ids: list[int],
) -> dict[int, list[float]]:

    games_statement = select(Game).where(
                    Game.steam_app_id.in_(steam_app_ids)
                    )

    games_result = await db.execute(games_statement)
    games = games_result.scalars().all()

    if games is None or len(games) == 0:
        return {}

    game_ids: list[int] = []
    game_texts: list[str] = []

    model = SentenceTransformer(MODEL_NAME)

    for game in games:
        text = build_game_embedding_text(game)

        if not text.strip():
            continue

        game_ids.append(game.steam_app_id)
        game_texts.append(text)

        if not game_texts:
            return {}

    embeddings = model.encode(
                game_texts,
                normalize_embeddings=True,
                batch_size=32,
                )

    return {
        steam_app_id: embedding.tolist()
        for steam_app_id, embedding in zip(
            game_ids, 
            embeddings,
            strict=True,
        )
    }



