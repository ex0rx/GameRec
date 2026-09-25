import asyncio

import httpx


class RequestPacer:
    def __init__(self, min_interval: float = 1.0):
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")

        self.min_interval = min_interval
        self.lock = asyncio.Lock()
        self.next_request_at = 0.0

    async def __call__(self, request: httpx.Request) -> None:
        async with self.lock:
            loop = asyncio.get_running_loop()
            now = loop.time()

            delay = max(0.0, self.next_request_at - now)

            if delay > 0:
                await asyncio.sleep(delay)

            self.next_request_at = loop.time() + self.min_interval
