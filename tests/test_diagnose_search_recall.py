"""Recall diagnostics distinguish Qdrant misses from filtered candidates."""

from unittest.mock import AsyncMock, Mock

import pytest

from gamerec.scripts import diagnose_search_recall
from gamerec.services.hybrid_search import HybridSearchResult, RankedSearchGame


def result():
    return HybridSearchResult(
        raw_candidates=[
            {"steam_app_id": 10, "name": "A", "score": 0.9},
            {"steam_app_id": 20, "name": "B", "score": 0.8},
            {"steam_app_id": 30, "name": "C", "score": 0.7},
        ],
        eligible_count=1,
        eligible_ids=frozenset({30}),
        missing_metadata_ids=frozenset({10}),
        ranked=[RankedSearchGame(30, "C", 0.7, 0.5, 0.9, 0.7)],
    )


def test_recall_positions_scores_and_statuses():
    rows = diagnose_search_recall.diagnose_expected_games(
        result(),
        [
            ("Missing metadata", 10),
            ("Filtered", 20),
            ("Ranked", 30),
            ("Absent", 40),
            ("Indexed outside pool", 50),
        ],
        {10, 20, 30, 50},
    )
    assert [
        (row.raw_rank, row.similarity_score, row.hybrid_rank, row.status)
        for row in rows
    ] == [
        (1, 0.9, None, "missing PostgreSQL metadata"),
        (2, 0.8, None, "filtered by minimum reviews"),
        (3, 0.7, 1, "ranked"),
        (None, None, None, "not indexed"),
        (None, None, None, "not in top 1000"),
    ]
    assert [row.indexed for row in rows] == [True, True, True, False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_script_closes_qdrant_and_db_on_failure(monkeypatch, fail):
    model = object()
    client = Mock(close=AsyncMock(), retrieve=AsyncMock(return_value=[]))
    db = object()
    session = AsyncMock()
    session.__aenter__.return_value = db
    search = AsyncMock(return_value=result())
    if fail:
        search.side_effect = RuntimeError("Qdrant unavailable")
    dispose = AsyncMock()
    monkeypatch.setattr(
        diagnose_search_recall, "load_embedding_model", Mock(return_value=model)
    )
    monkeypatch.setattr(diagnose_search_recall, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(diagnose_search_recall, "SessionLocal", lambda: session)
    monkeypatch.setattr(diagnose_search_recall, "engine", Mock(dispose=dispose))
    embed = Mock(return_value=[0.0] * 384)
    monkeypatch.setattr(diagnose_search_recall, "embed_search_query", embed)
    monkeypatch.setattr(diagnose_search_recall, "hybrid_search_games", search)

    if fail:
        with pytest.raises(RuntimeError, match="Qdrant unavailable"):
            await diagnose_search_recall.main()
        assert search.await_count == 1
    else:
        await diagnose_search_recall.main()
        assert search.await_count == 3
    for call in search.await_args_list:
        assert call.args == (db, client, [0.0] * 384)
        assert call.kwargs == {"candidate_k": 1000, "top_k": 1000}
    assert all(call.args[1] is model for call in embed.call_args_list)
    client.close.assert_awaited_once_with()
    assert client.retrieve.await_count == (0 if fail else 3)
    session.__aexit__.assert_awaited_once()
    dispose.assert_awaited_once_with()
