from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_time: int = 30,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_time = recovery_time
        self._failures = 0
        self._state = "closed"
        self._last_failure = 0.0
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            old_state = self._state
            self._failures = 0
            self._state = "closed"
            if old_state != "closed":
                logger.info("Circuit breaker closed: failures=%d", self._failures)

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure = time.time()
            old_state = self._state
            if self._failures >= self._failure_threshold:
                self._state = "open"
                logger.warning(
                    "Circuit breaker opened: failures=%d/%d, recovery_in=%ds",
                    self._failures,
                    self._failure_threshold,
                    self._recovery_time,
                )
            elif old_state == "half-open":
                logger.warning("Circuit breaker re-opened: failures=%d/%d", self._failures, self._failure_threshold)

    def should_allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.time() - self._last_failure > self._recovery_time:
                    self._state = "half-open"
                    self._failures = 0
                    logger.info("Circuit breaker half-open: probing for recovery")
                    return True
                return False
            return False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    @property
    def failure_threshold(self) -> int:
        with self._lock:
            return self._failure_threshold

    @property
    def recovery_time(self) -> int:
        with self._lock:
            return self._recovery_time


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

    def get_summary(self) -> dict[str, dict[str, str | int]]:
        with self._lock:
            return {
                key: {
                    "state": breaker.state,
                    "failures": breaker.failures,
                    "failure_threshold": breaker.failure_threshold,
                    "recovery_time": breaker.recovery_time,
                }
                for key, breaker in self._breakers.items()
            }
