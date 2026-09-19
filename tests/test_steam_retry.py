import asyncio

import httpx
import pytest

from gamerec.integrations.steam import get_with_retry


@pytest.mark.asyncio
async def test_retries_after_429(monkeypatch):
    # Record how many HTTP requests our function makes after 1 error 429.
    request_count = 0

    def mock_steam(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        if request_count == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "1"},
            )

        return httpx.Response(
            200,
            json={"success": True},
        )

    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(mock_steam)

    async with httpx.AsyncClient(transport=transport) as client:
        response = await get_with_retry(
            client=client,
            url="https://example.test/steam",
            params={"appids": 440},
        )

    assert response.status_code == 200
    assert request_count == 2

@pytest.mark.asyncio
async def test_retries_after_429_only(monkeypatch):
    # Record how many HTTP requests our function makes if it keeps getting error 429.
    request_count = 0

    def mock_steam(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        return httpx.Response(
            429,
            headers={"Retry-After": "1"},
        )

        
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(mock_steam)

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            response = await get_with_retry(
                client=client,
                url="https://example.test/steam",
                params={"appids": 440},
            )

    assert exc_info.value.response.status_code == 429
    assert request_count == 3