from __future__ import annotations

import threading
from datetime import UTC, datetime


class QuotaTracker:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._current_day: str = self._today()

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _maybe_reset(self) -> None:
        today = self._today()
        if today != self._current_day:
            self._counters.clear()
            self._current_day = today

    def _key(self, provider_id: str, model: str) -> str:
        return f"{provider_id}:{model}"

    def can_request(self, provider_id: str, model: str, limit: int = -1) -> bool:
        if limit == -1:
            return True

        with self._lock:
            self._maybe_reset()
            key = self._key(provider_id, model)
            return self._counters.get(key, 0) < limit

    def record(self, provider_id: str, model: str) -> None:
        with self._lock:
            self._maybe_reset()
            key = self._key(provider_id, model)
            self._counters[key] = self._counters.get(key, 0) + 1

    def get_usage(self, provider_id: str, model: str) -> int:
        with self._lock:
            self._maybe_reset()
            key = self._key(provider_id, model)
            return self._counters.get(key, 0)

    def get_all_usage(self) -> dict[str, int]:
        with self._lock:
            self._maybe_reset()
            return dict(self._counters)
