from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ProviderStats:

    requests: int = 0
    successes: int = 0
    failures: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    last_request: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.requests == 0:
            return 1.0
        return self.successes / self.requests

    @property
    def avg_latency_ms(self) -> float:
        if self.successes == 0:
            return 0.0
        return self.total_latency_ms / self.successes


class StatsTracker:

    def __init__(self) -> None:
        self._providers: dict[str, ProviderStats] = {}
        self._total_requests: int = 0
        self._start_time: float = time.time()

    def record_request(
        self,
        provider: str,
        success: bool,
        latency_ms: float,
        tokens: int = 0,
    ) -> None:
        if provider not in self._providers:
            self._providers[provider] = ProviderStats()

        stats = self._providers[provider]
        stats.requests += 1
        stats.last_request = time.time()

        if success:
            stats.successes += 1
            stats.total_tokens += tokens
            stats.total_latency_ms += latency_ms
        else:
            stats.failures += 1

        self._total_requests += 1

    def get_provider_stats(self, provider: str) -> ProviderStats | None:
        return self._providers.get(provider)

    def get_all_stats(self) -> dict[str, ProviderStats]:
        return self._providers.copy()

    def get_summary(self) -> dict[str, object]:
        return {
            "total_requests": self._total_requests,
            "uptime_seconds": int(time.time() - self._start_time),
            "providers": {
                name: {
                    "requests": s.requests,
                    "success_rate": round(s.success_rate, 3),
                    "avg_latency_ms": round(s.avg_latency_ms, 1),
                }
                for name, s in self._providers.items()
            },
        }
