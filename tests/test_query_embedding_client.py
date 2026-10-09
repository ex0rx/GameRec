"""Embedding bridge responses and failure cleanup use an async HTTP client."""

import httpx
import pytest

from gamerec.services import query_embedding_client

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "mode", ["ok", "server_error", "invalid", "wrong_size", "nonfinite", "network"]
)
async def test_bridge_client_closes_on_success_and_failure(monkeypatch, mode):
    real_client = httpx.AsyncClient
    clients = []

    def handler(request):
        assert request.url.path == "/embed/search"
        assert request.method == "POST"
        assert request.content == b'{"query":"survival"}'
        if mode == "network":
            raise httpx.ConnectError("unavailable")
        if mode == "server_error":
            return httpx.Response(503)
        if mode == "invalid":
            return httpx.Response(200, json={"bad": "response"})
        if mode == "wrong_size":
            return httpx.Response(200, json={"embedding": [1.0]})
        if mode == "nonfinite":
            return httpx.Response(200, json={"embedding": [float("inf")] * 384})
        return httpx.Response(200, json={"embedding": [0.0] * 384})

    def client_factory(*args, **kwargs):
        client = real_client(*args, transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(query_embedding_client.httpx, "AsyncClient", client_factory)
    if mode == "ok":
        assert (
            await query_embedding_client.get_query_embedding("survival") == [0.0] * 384
        )
    else:
        with pytest.raises(query_embedding_client.EmbeddingServiceUnavailable):
            await query_embedding_client.get_query_embedding("survival")
    assert len(clients) == 1
    assert clients[0].is_closed
