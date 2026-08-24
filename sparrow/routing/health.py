from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_time: int = 60,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_time = recovery_time
        self._failures = 0
        self._state = "closed"
        self._last_failure = 0.0
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure = time.time()
            if self._failures >= self._failure_threshold:
                self._state = "open"

    def should_allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.time() - self._last_failure > self._recovery_time:
                    self._state = "half-open"
                    self._failures = 0
                    return True
                return False
            return False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


class RouteHealthTracker:
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_breaker(self, key: str) -> CircuitBreaker:
        with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker()
            return self._breakers[key]

    def is_healthy(self, key: str) -> bool:
        return self.get_breaker(key).should_allow()
