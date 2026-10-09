"""The embeddings HTTP worker loads once and runs inference off the event loop."""

from unittest.mock import Mock

import httpx
import pytest

from gamerec import embedding_server

pytestmark = pytest.mark.asyncio


async def test_model_reused_and_inference_is_offloaded(monkeypatch):
    model = object()
    load = Mock(return_value=model)
    calls = []
    offloads = []

    async def fake_to_thread(function, *args):
        offloads.append((function, args))
        return function(*args)

    def encode(query, supplied_model):
        calls.append((query, supplied_model))
        if not query.strip():
            raise ValueError("Search query must not be empty")
        return [0.0] * 384

    monkeypatch.setattr(embedding_server.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(embedding_server, "load_embedding_model", load)
    monkeypatch.setattr(embedding_server, "embed_search_query", encode)
    async with embedding_server.lifespan(embedding_server.app):
        transport = httpx.ASGITransport(app=embedding_server.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post("/embed/search", json={"query": "survival"})
            second = await client.post("/embed/search", json={"query": "crafting"})
            invalid = await client.post("/embed/search", json={"query": "   "})

    assert first.status_code == second.status_code == 200
    assert len(first.json()["embedding"]) == 384
    assert invalid.status_code == 422
    load.assert_called_once_with()
    assert [call[0] for call in calls] == ["survival", "crafting", "   "]
    assert all(call[1] is model for call in calls)
    assert offloads == [
        (load, ()),
        (encode, ("survival", model)),
        (encode, ("crafting", model)),
        (encode, ("   ", model)),
    ]
    assert not hasattr(embedding_server.app.state, "model")
