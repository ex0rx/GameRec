"""Compare indexed retrieval with the unchanged PostgreSQL cosine baseline."""

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.services.game_similarity import find_similar_games as baseline
from gamerec.services.vector_store import ensure_game_collection, find_similar_games
from gamerec.services.vector_sync import sync_embeddings_to_qdrant

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_synced_retrieval_matches_phase5_baseline(pg_sessions, monkeypatch):
    monkeypatch.setattr(
        settings, "qdrant_game_collection", "isolated-baseline-comparison"
    )
    vectors = {90: (1.0, 0.0), 55: (3.0, 4.0), 10: (0.0, 1.0), 70: (-1.0, 0.0)}
    async with pg_sessions() as db, db.begin():
        db.add_all([Game(steam_app_id=i, name=f"Game {i}") for i in vectors])
        await db.flush()
        db.add_all(
            [
                GameEmbedding(
                    steam_app_id=i,
                    model_name=settings.embeddings_model_name,
                    model_revision=settings.embeddings_model_revision,
                    input_hash="a" * 64,
                    embedding=[x, y] + [0.0] * 382,
                )
                for i, (x, y) in vectors.items()
            ]
        )
    client = AsyncQdrantClient(":memory:")
    try:
        await ensure_game_collection(client)
        async with pg_sessions() as db:
            await sync_embeddings_to_qdrant(db, client, batch_size=2)
            for target in vectors:
                expected = await baseline(db, target, top_k=3)
                actual = await find_similar_games(client, target, top_k=3)
                # Equal scores have no defined tie ordering in either service.
                assert {r["steam_app_id"] for r in actual} == {i for i, _ in expected}
                assert [r["score"] for r in actual] == pytest.approx(
                    [s for _, s in expected]
                )
                scores = dict(expected)
                for result in actual:
                    assert result["score"] == pytest.approx(
                        scores[result["steam_app_id"]]
                    )
                    assert result["name"] == f"Game {result['steam_app_id']}"
    finally:
        await client.close()
