"""Snapshot selection and kernel parity with the unchanged Phase 5 service."""

import pytest

from gamerec.core.config import settings
from gamerec.models.game import Game
from gamerec.models.game_embedding import GameEmbedding
from gamerec.scripts.benchmark_retrieval import (
    exact_search,
    load_snapshot,
    select_queries,
)
from gamerec.services.game_similarity import find_similar_games

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_snapshot_is_bounded_versioned_and_matches_baseline(pg_sessions):
    vectors = {10: [1.0, 0.0], 20: [0.8, 0.6], 30: [0.0, 1.0], 40: [-1.0, 0.0]}
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
                    embedding=v,
                )
                for i, v in vectors.items()
            ]
        )
        db.add(
            GameEmbedding(
                steam_app_id=10,
                model_name=settings.embeddings_model_name,
                model_revision="other",
                input_hash="b" * 64,
                embedding=[-1.0, 0.0],
            )
        )
    async with pg_sessions() as db:
        snapshot = await load_snapshot(db, 2, [40, 999])
        assert list(snapshot) == [10, 20, 40]
        assert snapshot[10] == vectors[10]
        assert select_queries([40, 999], snapshot) == ([40], [999])
        full = await load_snapshot(db, 10, [])
        for query, vector in full.items():
            expected = await find_similar_games(db, query, top_k=3)
            assert exact_search(full, query, vector, 3) == [i for i, _ in expected]
