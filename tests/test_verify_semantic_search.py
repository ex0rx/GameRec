"""Manual verification script reuses one model and closes its Qdrant client."""

from unittest.mock import AsyncMock, Mock

import pytest

from gamerec.scripts import verify_semantic_search

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("fail_on_search", [False, True])
async def test_verification_script_closes_client(monkeypatch, capsys, fail_on_search):
    model = object()
    client = AsyncMock()
    load_model = Mock(return_value=model)
    embed = Mock(side_effect=lambda query, supplied_model: [float(len(query))])
    search = AsyncMock(
        return_value=[{"steam_app_id": 10, "name": "Example", "score": 0.8}]
    )
    if fail_on_search:
        search.side_effect = RuntimeError("unavailable")

    monkeypatch.setattr(verify_semantic_search, "load_embedding_model", load_model)
    monkeypatch.setattr(verify_semantic_search, "embed_search_query", embed)
    monkeypatch.setattr(verify_semantic_search, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(verify_semantic_search, "search_games_by_embedding", search)

    if fail_on_search:
        with pytest.raises(RuntimeError, match="unavailable"):
            await verify_semantic_search.main()
        assert embed.call_count == 1
        assert search.await_count == 1
    else:
        await verify_semantic_search.main()
        assert embed.call_count == 3
        assert search.await_count == 3
        assert "Steam App ID: 10" in capsys.readouterr().out

    load_model.assert_called_once_with()
    for call in embed.call_args_list:
        assert call.args[1] is model
    for call in search.await_args_list:
        assert call.args[0] is client
        assert call.kwargs == {"top_k": 10}
    client.close.assert_awaited_once_with()
