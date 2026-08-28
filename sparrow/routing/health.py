from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    failures: int = 0
    state: str = "closed"
    last_failure: float = 0.0
    failure_threshold: int = 5
    recovery_time: int = 30


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
            if old_state == "half-open":
                self._state = "open"
                logger.warning("Circuit breaker re-opened: failures=%d/%d", self._failures, self._failure_threshold)
            elif self._failures >= self._failure_threshold:
                self._state = "open"
                logger.warning(
                    "Circuit breaker opened: failures=%d/%d, recovery_in=%ds",
                    self._failures,
                    self._failure_threshold,
                    self._recovery_time,
                )

    def should_allow(self) -> bool:
        return self.try_acquire()

    def try_acquire(self) -> bool:
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

    def is_eligible(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                return time.time() - self._last_failure > self._recovery_time
            return False

    def cancel_acquire(self) -> None:
        with self._lock:
            if self._state == "half-open":
                self._state = "open"
                self._last_failure = time.time()

    def to_state(self) -> CircuitBreakerState:
        with self._lock:
            return CircuitBreakerState(
                failures=self._failures,
                state=self._state,
                last_failure=self._last_failure,
                failure_threshold=self._failure_threshold,
                recovery_time=self._recovery_time,
            )

    def load_state(self, saved: CircuitBreakerState) -> None:
        with self._lock:
            self._failures = saved.failures
            self._state = saved.state
            self._last_failure = saved.last_failure
            self._failure_threshold = saved.failure_threshold
            self._recovery_time = saved.recovery_time

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
    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._load_from_disk()

    def get_breaker(self, key: str) -> CircuitBreaker:
        with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker()
            return self._breakers[key]

    def is_healthy(self, key: str) -> bool:
        return self.is_eligible(key)

    def is_eligible(self, key: str) -> bool:
        return self.get_breaker(key).is_eligible()

    def try_acquire(self, key: str) -> bool:
        acquired = self.get_breaker(key).try_acquire()
        if acquired:
            self._maybe_persist()
        return acquired

    def cancel_acquire(self, key: str) -> None:
        self.get_breaker(key).cancel_acquire()
        self._maybe_persist()

    def record_success(self, key: str) -> None:
        breaker = self.get_breaker(key)
        breaker.record_success()
        self._maybe_persist()

    def record_failure(self, key: str) -> None:
        breaker = self.get_breaker(key)
        breaker.record_failure()
        self._maybe_persist()

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

    def _maybe_persist(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_to_disk()
        except Exception:
            logger.debug("Failed to persist circuit breaker state", exc_info=True)

    def _persist_to_disk(self) -> None:
        if self._persist_path is None:
            return
        with self._lock:
            data = {key: asdict(breaker.to_state()) for key, breaker in self._breakers.items()}
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._persist_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self._persist_path)

    def _load_from_disk(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load circuit breaker state from %s", self._persist_path)
            return

        for key, saved in raw.items():
            try:
                state = CircuitBreakerState(**saved)
                breaker = CircuitBreaker(
                    failure_threshold=state.failure_threshold,
                    recovery_time=state.recovery_time,
                )
                breaker.load_state(state)
                self._breakers[key] = breaker
            except (TypeError, KeyError):
                logger.debug("Skipping invalid breaker state for %s", key)

        logger.info("Loaded %d circuit breakers from %s", len(self._breakers), self._persist_path)
