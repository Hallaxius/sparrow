from __future__ import annotations

import asyncio
import time
from collections import deque

DEFAULT_RPM = 36
_MAX_WAIT_SECONDS = 5.0


class RpmGovernor:
    def __init__(self, default_rpm: int = DEFAULT_RPM) -> None:
        self._default_rpm = default_rpm
        self._buckets: dict[str, deque[float]] = {}

    async def acquire(self, provider_id: str, rpm: int | None = None) -> None:
        if rpm is None:
            rpm = self._default_rpm
        if rpm <= 0:
            return
        now = time.monotonic()
        window = 60.0
        limit = float(rpm)
        timestamps = self._buckets.setdefault(provider_id, deque())
        while timestamps and timestamps[0] <= now - window:
            timestamps.popleft()
        if len(timestamps) < limit:
            timestamps.append(now)
            return
        wait = max(0.0, timestamps[0] + window - now)
        if wait > _MAX_WAIT_SECONDS:
            wait = _MAX_WAIT_SECONDS
        await asyncio.sleep(wait)
        now = time.monotonic()
        self._buckets[provider_id] = deque(t for t in timestamps if t > now - window)
        self._buckets[provider_id].append(now)
