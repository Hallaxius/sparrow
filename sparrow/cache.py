from __future__ import annotations

import hashlib
import json
import time
from typing import Any


class ResponseCache:

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size

    def _make_key(self, provider: str, model: str, body: dict[str, object]) -> str:
        content = f"{provider}:{model}:{json.dumps(body, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get(self, provider: str, model: str, body: dict[str, object]) -> Any | None:
        key = self._make_key(provider, model, body)
        if key in self._cache:
            expires, data = self._cache[key]
            if time.time() < expires:
                return data
            del self._cache[key]
        return None

    def set(self, provider: str, model: str, body: dict[str, object], response: Any) -> None:
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]
        key = self._make_key(provider, model, body)
        self._cache[key] = (time.time() + self._ttl, response)

    def clear(self) -> None:
        self._cache.clear()
