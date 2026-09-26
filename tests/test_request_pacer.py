import asyncio

import httpx
import pytest

from gamerec.integrations.request_pacer import RequestPacer
from gamerec.integrations.steam import get_with_retry


@pytest.mark.asyncio
async def test_pacer_spaces_concurrent_requests_including_retries():
    request_times = []
    attempts = {}

    async def mock_steam(
        request: httpx.Request,
    ) -> httpx.Response:
        # Record when this request reaches the mock transport.
        now = asyncio.get_running_loop().time()
        request_times.append(now)

        appid = request.url.params["appid"]

        attempts[appid] = attempts.get(appid, 0) + 1

        # Game 10 fails once, then succeeds on its retry.
        if appid == "10" and attempts[appid] == 1:
            return httpx.Response(429)

        return httpx.Response(
            200,
            json={"success": True},
        )

    min_interval = 0.03

    pacer = RequestPacer(
        min_interval=min_interval,
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(mock_steam),
        event_hooks={"request": [pacer]},
    ) as client:
        responses = await asyncio.gather(
            *(
                get_with_retry(
                    client=client,
                    url="https://example.test/steam",
                    params={"appid": appid},
                    max_attempts=2,
                )
                for appid in [10, 20, 30]
            )
        )

    # Three original requests + one retry for game 10.
    assert len(request_times) == 4

    assert attempts == {
        "10": 2,
        "20": 1,
        "30": 1,
    }

    assert all(response.status_code == 200 for response in responses)

    # Measure the interval between successive request starts.
    intervals = [
        later - earlier
        for earlier, later in zip(  # noqa RUF007
            request_times,
            request_times[1:],
        )
    ]

    # Small tolerance for clock/scheduling precision.
    assert all(interval >= min_interval - 0.005 for interval in intervals)
