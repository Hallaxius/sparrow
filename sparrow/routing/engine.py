from __future__ import annotations

from enum import Enum

from sparrow.errors import AllProvidersExhaustedError
from sparrow.routing.health import RouteHealthTracker
from sparrow.routing.quota import QuotaTracker


class RoutingMode(Enum):
    FAIR = "fair"
    FAST = "fast"
    QUALITY = "quality"
    MODEL = "model"

class Route:

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        quality: int = 5,
        context_window: int = 128000,
        avg_latency_ms: float = 0.0,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.quality = quality
        self.context_window = context_window
        self.avg_latency_ms = avg_latency_ms

class RoutingEngine:

    def __init__(self, health_tracker: RouteHealthTracker | None = None, quota: QuotaTracker | None = None) -> None:
        self._routes: list[Route] = []
        self._rr_indices: dict[str, int] = {}
        self._health = health_tracker
        self._quota = quota

    @property
    def route_count(self) -> int:
        return len(self._routes)

    def register_route(self, route: Route) -> None:
        self._routes.append(route)

    def get_candidates(self, model: str, max_tokens: int | None = None) -> list[Route]:
        if model == "auto" or model == "fair":
            candidates = list(self._routes)
        else:
            candidates = [r for r in self._routes if r.model_id == model]

        candidates = self._healthy_candidates(candidates)

        if self._quota is not None:
            candidates = [
                r for r in candidates
                if self._quota.can_request(r.provider_id, r.model_id)
            ]

        candidates = self._filter_by_context(candidates, max_tokens)

        return candidates

    def _healthy_candidates(self, candidates: list[Route]) -> list[Route]:
        if self._health is None:
            return candidates
        return [
            r
            for r in candidates
            if self._health.is_healthy(f"{r.provider_id}:{r.model_id}")
        ]

    def _filter_by_context(
        self, candidates: list[Route], max_tokens: int | None
    ) -> list[Route]:
        if max_tokens is None:
            return candidates
        return [r for r in candidates if r.context_window >= max_tokens]

    def select(
        self,
        model: str,
        mode: RoutingMode = RoutingMode.FAIR,
        max_tokens: int | None = None,
    ) -> Route:
        candidates = self.get_candidates(model, max_tokens=max_tokens)
        if not candidates:
            raise AllProvidersExhaustedError(model)

        if mode == RoutingMode.FAIR:
            index = self._rr_indices.get(model, 0)
            route = candidates[index % len(candidates)]
            self._rr_indices[model] = index + 1
            return route
        elif mode == RoutingMode.FAST:
            return min(candidates, key=lambda r: r.avg_latency_ms)
        elif mode == RoutingMode.QUALITY:
            return max(candidates, key=lambda r: r.quality)
        else:
            return candidates[0]
