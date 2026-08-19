from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self._window
        ]
        if len(self._requests[client_ip]) >= self._max_requests:
            return False
        self._requests[client_ip].append(now)
        return True
