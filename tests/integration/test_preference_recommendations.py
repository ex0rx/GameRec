"""Persisted feedback flows through the actual recommendation pipeline.

Only Qdrant HTTP is mocked. PostgreSQL supplies ownership, preferences, vectors
and ranking metadata; no Steam/Ollama/embedding generation takes place.
"""

from datetime import date
from math import hypot, isfinite
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient

from gamerec.core.config import settings
from gamerec.models import Game, GameEmbedding, User, UserGame
from gamerec.services import personalised_recommendation as recommendations
from gamerec.services.user_profile import build_user_profile_vector

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
USER = "76561190000000001"


async def test_persisted_like_dislike_clear_change_real_pipeline_signals(
    pg_sessions, pg_client, monkeypatch
):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "user_preference_writes_enabled", True)
    monkeypatch.setattr(settings, "steamid64_test", USER)
    monkeypatch.setattr(settings, "embeddings_vector_size", 3)
    vectors = {
        10: [1.0, 0.0, 0.0],
        20: [0.0, 1.0, 0.0],
        30: [0.0, 0.0, 1.0],
        101: [1.0, 0.0, 0.0],
        102: [0.0, 1.0, 0.0],
    }
    genres = {10: "Action", 20: "Puzzle", 30: "Strategy", 101: "Action", 102: "Puzzle"}
    async with pg_sessions() as db:
        db.add(User(steamid64=USER))
        db.add_all(
            Game(
                steam_app_id=appid,
                name=f"Synthetic {appid}",
                genres=[genres[appid]],
                categories=["Single-player"],
                total_reviews=1000,
                total_positive=900,
                total_negative=100,
                metadata_available=True,
                release_date=date(2020, 1, 1),
            )
            for appid in vectors
        )
        await db.commit()
        db.add_all(
            GameEmbedding(
                steam_app_id=appid,
                model_name=settings.embeddings_model_name,
                model_revision=settings.embeddings_model_revision,
                input_hash="a" * 64,
                embedding=vector,
            )
            for appid, vector in vectors.items()
        )
        db.add_all(
            UserGame(
                steamid64=USER,
                steam_app_id=appid,
                playtime_forever_minutes=300,
            )
            for appid in (10, 20, 30)
        )
        await db.commit()

    clients = []

    def qdrant():
        client = AsyncMock(spec=AsyncQdrantClient)
        client.query_points.return_value = SimpleNamespace(
            points=[
                SimpleNamespace(
                    id=appid, score=0.5, payload={"name": f"Synthetic {appid}"}
                )
                for appid in (101, 102)
            ]
        )
        clients.append(client)
        return client

    monkeypatch.setattr(recommendations, "get_qdrant_client", qdrant)

    async def evaluate():
        async with pg_sessions() as db:
            result = await recommendations.get_user_recommendations(db, USER)
        async with pg_sessions() as db:
            profile, owned = await build_user_profile_vector(db, USER)
        assert owned == {10, 20, 30}
        assert hypot(*profile) == pytest.approx(1)
        assert all(isfinite(game.hybrid_score) for game in result)
        return {game.steam_app_id: game for game in result}, profile

    baseline, base_profile = await evaluate()
    assert baseline[101].similarity_score == pytest.approx(
        baseline[102].similarity_score
    )
    response = await pg_client.put(
        f"/users/{USER}/preferences/10", json={"preference": "liked"}
    )
    assert response.status_code == 200
    liked, liked_profile = await evaluate()
    assert liked_profile[0] > base_profile[0]
    assert liked[101].similarity_score > baseline[101].similarity_score
    assert liked[101].hybrid_score > baseline[101].hybrid_score
    assert liked[101].hybrid_score > liked[102].hybrid_score

    response = await pg_client.put(
        f"/users/{USER}/preferences/20", json={"preference": "disliked"}
    )
    assert response.status_code == 200
    disliked, disliked_profile = await evaluate()
    assert disliked_profile[1] < 0
    assert disliked[102].similarity_score < 0
    assert disliked[102].affinity_score < liked[102].affinity_score
    assert disliked[102].hybrid_score < liked[102].hybrid_score
    # Components independent of personal feedback do not change.
    for appid in (101, 102):
        for field in ("popularity_score", "review_quality", "recency_score"):
            assert getattr(disliked[appid], field) == getattr(baseline[appid], field)
    for appid in (10, 20):
        assert (
            await pg_client.delete(f"/users/{USER}/preferences/{appid}")
        ).status_code == 204
    restored, restored_profile = await evaluate()
    assert restored_profile == pytest.approx(base_profile)
    assert restored == baseline
    assert (await pg_client.get(f"/users/{USER}/preferences")).json() == []
    for client in clients:
        client.close.assert_awaited_once()
    # Unmodified Phase 8 profile retrieval budget is still used.
    assert all(
        client.query_points.await_args_list[0].kwargs["limit"] == 5000
        for client in clients
    )
