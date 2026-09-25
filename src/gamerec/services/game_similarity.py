from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.ml.similarity import cosine_similarity
from gamerec.models.game_embedding import GameEmbedding


async def find_similar_games(
    db: AsyncSession,
    steam_app_id: int,
    top_k: int = 5,
) -> list[tuple[int, float]]:

    game_embedding_statement = select(GameEmbedding).where(
        GameEmbedding.steam_app_id == steam_app_id,
        GameEmbedding.model_name == settings.embeddings_model_name,
        GameEmbedding.model_revision == settings.embeddings_model_revision,
    )

    game_embedding_result = await db.execute(game_embedding_statement)
    game_embedding = game_embedding_result.scalar_one_or_none()

    if not game_embedding:
        return []

    similarity_statement = select(
        GameEmbedding.steam_app_id,
        GameEmbedding.embedding,
    ).where(
        GameEmbedding.steam_app_id != steam_app_id,
        GameEmbedding.model_name == settings.embeddings_model_name,
        GameEmbedding.model_revision == settings.embeddings_model_revision,
    )

    similarity_result = await db.execute(similarity_statement)
    candidates = similarity_result.all()

    similar_games = []

    for candidate_steam_app_id, candidate_embedding in candidates:
        similarity_score = cosine_similarity(
            game_embedding.embedding,
            candidate_embedding,
        )

        similar_games.append((candidate_steam_app_id, similarity_score))

    similar_games.sort(key=lambda x: x[1], reverse=True)

    return similar_games[:top_k]
