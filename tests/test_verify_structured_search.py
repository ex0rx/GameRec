"""The read-only verification script reuses a model and closes resources."""

from unittest.mock import AsyncMock, Mock

import pytest

from gamerec.scripts import verify_structured_search
from gamerec.services.hybrid_search import HybridSearchResult


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_script_closes_qdrant_and_database(monkeypatch, fail):
    model = object()
    client = Mock(close=AsyncMock())
    db = object()
    session = AsyncMock()
    session.__aenter__.return_value = db
    dispose = AsyncMock()
    search = AsyncMock(
        return_value=HybridSearchResult([], 0, frozenset(), frozenset(), [])
    )
    if fail:
        search.side_effect = RuntimeError("Qdrant unavailable")
    load = Mock(return_value=model)
    embed = Mock(return_value=[0.0] * 384)
    monkeypatch.setattr(verify_structured_search, "load_embedding_model", load)
    monkeypatch.setattr(verify_structured_search, "embed_search_query", embed)
    monkeypatch.setattr(verify_structured_search, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(verify_structured_search, "SessionLocal", lambda: session)
    monkeypatch.setattr(verify_structured_search, "engine", Mock(dispose=dispose))
    monkeypatch.setattr(verify_structured_search, "hybrid_search_games", search)

    if fail:
        with pytest.raises(RuntimeError, match="Qdrant unavailable"):
            await verify_structured_search.main()
        assert search.await_count == 1
    else:
        await verify_structured_search.main()
        assert search.await_count == 5
    load.assert_called_once_with()
    embed.assert_called_once_with(verify_structured_search.QUERY, model)
    for call in search.await_args_list:
        assert call.args == (db, client, [0.0] * 384)
        assert call.kwargs["top_k"] == 10
    client.close.assert_awaited_once_with()
    session.__aexit__.assert_awaited_once()
    dispose.assert_awaited_once_with()
