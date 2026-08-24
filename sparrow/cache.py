from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


class ResponseCache:
    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._cache: dict[str, tuple[float, int, Any]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._sequence = 0
        self._lock = threading.RLock()

    def _make_key(self, provider: str, model: str, body: Mapping[str, object], scope: str) -> str:
        material = {
            "provider": provider,
            "model": model,
            "request": body,
            "scope": scope,
        }
        content = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def get(
        self,
        provider: str,
        model: str,
        body: Mapping[str, object],
        scope: str = "anonymous",
    ) -> Any | None:
        with self._lock:
            key = self._make_key(provider, model, body, scope)
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires, _, data = entry
            if time.monotonic() >= expires:
                del self._cache[key]
                return None
            return deepcopy(data)

    def set(
        self,
        provider: str,
        model: str,
        body: Mapping[str, object],
        response: Any,
        scope: str = "anonymous",
    ) -> None:
        with self._lock:
            now = time.monotonic()
            expired = [key for key, (expires, _, _) in self._cache.items() if now >= expires]
            for key in expired:
                del self._cache[key]

            key = self._make_key(provider, model, body, scope)
            if key not in self._cache and len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda item: (self._cache[item][0], self._cache[item][1]))
                del self._cache[oldest_key]

            self._sequence += 1
            self._cache[key] = (now + self._ttl, self._sequence, deepcopy(response))

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
