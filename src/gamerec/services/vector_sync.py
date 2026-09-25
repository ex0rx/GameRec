from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services.vector_store import (
    delete_game_points,
    get_game_point_payloads,
    scroll_game_points,
    upsert_game_points,
    validate_game_collection,
)


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
    *,
    collection_name: str | None = None,
    prune_missing: bool = False,
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

    if prune_missing and max_games is not None:
        raise ValueError("prune_missing requires a full sync without max_games")
    name = collection_name or settings.qdrant_game_collection
    await validate_game_collection(client, collection_name=name)
    last_steam_app_id = 0
    processed = upserted = batches = inserted = updated = skipped = 0
    while max_games is None or processed < max_games:
        limit = batch_size
        if max_games is not None:
            limit = min(limit, max_games - processed)
        records = await get_embedding_sync_batch(db, last_steam_app_id, limit)
        if not records:
            break
        points = build_qdrant_points(records, settings.embeddings_vector_size)
        existing = await get_game_point_payloads(
            client, [record.steam_app_id for record in records], collection_name=name
        )
        changed = [
            point
            for point in points
            if point.id not in existing
            or any(
                existing[point.id].get(key) != value
                for key, value in (point.payload or {}).items()
            )
        ]
        upserted += await upsert_game_points(client, changed, collection_name=name)
        new_count = sum(point.id not in existing for point in changed)
        inserted += new_count
        updated += len(changed) - new_count
        skipped += len(points) - len(changed)
        processed += len(records)
        batches += 1
        last_steam_app_id = records[-1].steam_app_id

    deleted = 0
    if prune_missing:
        pruning = await prune_embeddings_from_qdrant(
            db, client, batch_size=batch_size, collection_name=name, dry_run=False
        )
        deleted = pruning["deleted"]
    return {
        "processed": processed,
        "upserted": upserted,
        "batches": batches,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "deleted": deleted,
    }


async def prune_embeddings_from_qdrant(
    db: AsyncSession,
    client: AsyncQdrantClient,
    *,
    batch_size: int = 32,
    collection_name: str | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Compare each Qdrant page to the complete current-model PostgreSQL set.

    Defaults to preview only. Run with a dedicated session against committed
    source data and pause concurrent embedding/sync writers during pruning.
    There is no cross-store transaction; failures leave completed batches intact.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if db.new or db.dirty or db.deleted:
        raise ValueError("Pruning requires a clean session with committed source data")
    name = collection_name or settings.qdrant_game_collection
    await validate_game_collection(client, collection_name=name)
    offset = None
    scanned = candidates = deleted = batches = 0
    while True:
        points, next_offset = await scroll_game_points(
            client, collection_name=name, batch_size=batch_size, offset=offset
        )
        if not points:
            break
        if any(not isinstance(point.id, int) for point in points):
            raise ValueError("Collection contains non-Steam point IDs; pruning stopped")
        ids = [point.id for point in points]
        with db.no_autoflush:
            result = await db.scalars(
                select(GameEmbedding.steam_app_id).where(
                    GameEmbedding.steam_app_id.in_(ids),
                    GameEmbedding.model_name == settings.embeddings_model_name,
                    GameEmbedding.model_revision == settings.embeddings_model_revision,
                )
            )
        present = set(result.all())
        missing = [appid for appid in ids if appid not in present]
        scanned += len(ids)
        candidates += len(missing)
        batches += 1
        if not dry_run:
            deleted += await delete_game_points(client, missing, collection_name=name)
        if next_offset is None:
            break
        offset = next_offset
    return {
        "scanned": scanned,
        "candidates": candidates,
        "deleted": deleted,
        "batches": batches,
    }
