import hashlib

from sentence_transformers import SentenceTransformer
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.ml.game_text import build_game_embedding_text
from gamerec.ml.similarity import cosine_similarity
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding

EXPECTED_DIMENSIONS = 384

async def generate_game_embeddings(
    db: AsyncSession,
    steam_app_ids: list[int],
    model: SentenceTransformer,
) -> tuple[dict[int, list[float]], dict[int, str]]:

    if not steam_app_ids:
        return {}, {}
    
    games_statement = select(Game).where(
                    Game.steam_app_id.in_(steam_app_ids)
                    )

    games_result = await db.execute(games_statement)
    games = games_result.scalars().all()

    if not games:
        return {}, {}

    existing_embeddings_statement = (select(
                        GameEmbedding.steam_app_id,
                        GameEmbedding.input_hash)
                        .where(
                            GameEmbedding.steam_app_id.in_(steam_app_ids),
                            GameEmbedding.model_name == settings.embeddings_model_name,
                            GameEmbedding.model_revision == settings.embeddings_model_revision,
                        ))
    existing_embeddings_result = await db.execute(existing_embeddings_statement)
    existing_embeddings = {
        steam_app_id: input_hash
        for steam_app_id, input_hash in existing_embeddings_result.all()
    }

    game_ids: list[int] = []
    game_texts: list[str] = []
    input_hashes: dict[int, str] = {}


    for game in games:
        text = build_game_embedding_text(game)

        if not text.strip():
            continue

        current_game_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if current_game_hash == existing_embeddings.get(game.steam_app_id):
            continue

        game_ids.append(game.steam_app_id)
        game_texts.append(text)
        input_hashes[game.steam_app_id] = current_game_hash

    if not game_texts:
        return {}, {}

    embeddings = model.encode(
                game_texts,
                normalize_embeddings=True,
                batch_size=32,
                )

    embeddings_dict = {
        steam_app_id: embedding.tolist()
            for steam_app_id, embedding in zip(
                game_ids, 
                embeddings,
                strict=True,
            )
    }

    return embeddings_dict, input_hashes

async def save_game_embeddings(
    db: AsyncSession,
    embeddings: dict[int, list[float]],
    input_hashes: dict[int, str],
    model_name: str,
    model_revision: str,
) -> int:
    if not embeddings:
        return 0

    for steam_app_id, embedding in embeddings.items():
        if len(embedding) != EXPECTED_DIMENSIONS:
            raise ValueError(
                f"Invalid embedding dimensions for {steam_app_id}: "
                f"{len(embedding)}"
            )

    game_embedding_rows = [
        {
            "steam_app_id": steam_app_id,
            "model_name": model_name,
            "model_revision": model_revision,
            "input_hash": input_hashes[steam_app_id],
            "embedding": embedding,
        }
        for steam_app_id, embedding in embeddings.items()
    ]

    statement = insert(GameEmbedding).values(game_embedding_rows)

    statement = statement.on_conflict_do_update(
            index_elements=[
                GameEmbedding.steam_app_id,
                GameEmbedding.model_name,
                GameEmbedding.model_revision,
            ],
            set_={
                "input_hash": statement.excluded.input_hash,
                "embedding": statement.excluded.embedding,
                "created_at": func.now(),
            },
        )
    await db.execute(statement)

    return len(game_embedding_rows)
    
async def get_next_embedding_batch(
    db: AsyncSession,
    last_game_id: int = 0,
    batch_size: int = 32,
) -> list[Game]:
    statement = (
        select(Game)
        .where(
            Game.id > last_game_id,
            Game.metadata_available.is_(True),
            Game.short_description.is_not(None),
            func.length(func.trim(Game.short_description)) > 0,
        )
        .order_by(Game.id)
        .limit(batch_size)
    )

    result = await db.execute(statement)
    return list(result.scalars().all())


async def process_game_embeddings(
    db: AsyncSession,
    model: SentenceTransformer,
    batch_size: int = 32,
    max_games: int | None = None,
) -> dict[str, int]:

    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    if max_games is not None and max_games <= 0:
        raise ValueError("max_games must be a positive integer or None")

    last_game_id = 0
    processed = 0
    generated = 0

    while True:
        current_batch_size = batch_size

        if max_games is not None:
            remaining = max_games - processed

            if remaining <= 0:
                break
            current_batch_size = min(current_batch_size, remaining)

        async with db.begin():
            games_batch = await get_next_embedding_batch(
                db=db,
                last_game_id=last_game_id,
                batch_size=current_batch_size,
            )

            if not games_batch:
                break

            steam_app_ids = [game.steam_app_id for game in games_batch]

            embeddings, input_hashes = await generate_game_embeddings(
                db=db,
                steam_app_ids=steam_app_ids,
                model=model,
            )

            saved_count = await save_game_embeddings(
                db=db,
                embeddings=embeddings,
                input_hashes=input_hashes,
                model_name=settings.embeddings_model_name,
                model_revision=settings.embeddings_model_revision,
            )

        processed += len(games_batch)
        generated += saved_count
        last_game_id = games_batch[-1].id


    return {
        "processed": processed,
        "generated": generated,
        "skipped": processed - generated,
    }
