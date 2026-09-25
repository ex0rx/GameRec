from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services.vector_store import upsert_game_points


@dataclass(frozen=True)
class EmbeddingSyncRecord:
    steam_app_id: int
    embedding: list[float]
    input_hash: str
    name: str | None
    genres: list[str] | None
    categories: list[str] | None


async def get_embedding_sync_batch(
    db: AsyncSession,
    last_steam_app_id: int = 0,
    batch_size: int = 32,
) -> list[EmbeddingSyncRecord]:
    """Read one app-ID ordered page for the configured model and revision."""
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    statement = (
        select(
            GameEmbedding.steam_app_id,
            GameEmbedding.embedding,
            GameEmbedding.input_hash,
            Game.name,
            Game.genres,
            Game.categories,
        )
        .join(Game, Game.steam_app_id == GameEmbedding.steam_app_id)
        .where(
            GameEmbedding.model_name == settings.embeddings_model_name,
            GameEmbedding.model_revision == settings.embeddings_model_revision,
            GameEmbedding.steam_app_id > last_steam_app_id,
        )
        .order_by(GameEmbedding.steam_app_id)
        .limit(batch_size)
    )
    # Sync only reads persisted data
    with db.no_autoflush:
        result = await db.execute(statement)
    return [EmbeddingSyncRecord(*row) for row in result.all()]


def build_qdrant_points(
    records: Sequence[EmbeddingSyncRecord],
    expected_dimensions: int = settings.embeddings_vector_size,
) -> list[PointStruct]:
    """Pure transformation; nullable metadata becomes empty payload values."""
    points = []
    for record in records:
        if len(record.embedding) != expected_dimensions:
            raise ValueError(
                f"Invalid embedding dimensions for {record.steam_app_id}: "
                f"{len(record.embedding)} (expected {expected_dimensions})"
            )
        points.append(
            PointStruct(
                id=record.steam_app_id,
                vector=list(record.embedding),
                payload={
                    "steam_app_id": record.steam_app_id,
                    "name": record.name or "",
                    "genres": list(record.genres or []),
                    "categories": list(record.categories or []),
                    "input_hash": record.input_hash,
                },
            )
        )
    return points


async def sync_embeddings_to_qdrant(
    db: AsyncSession,
    client: AsyncQdrantClient,
    batch_size: int = 32,
    max_games: int | None = None,
) -> dict[str, int]:
    """Upsert into an existing collection; caller owns session/client lifetime.

    No PostgreSQL writes or commits. Failures propagate; completed Qdrant batches
    remain available and a rerun safely overwrites the same app IDs. The cursor
    is run-local, so concurrent source changes behind it are picked up next run.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if max_games is not None and max_games <= 0:
        raise ValueError("max_games must be a positive integer or None")

    last_steam_app_id = 0
    processed = upserted = batches = 0
    while max_games is None or processed < max_games:
        limit = batch_size
        if max_games is not None:
            limit = min(limit, max_games - processed)
        records = await get_embedding_sync_batch(db, last_steam_app_id, limit)
        if not records:
            break
        points = build_qdrant_points(records, settings.embeddings_vector_size)
        upserted += await upsert_game_points(client, points)
        processed += len(records)
        batches += 1
        last_steam_app_id = records[-1].steam_app_id

    return {"processed": processed, "upserted": upserted, "batches": batches}
