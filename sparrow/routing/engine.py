from __future__ import annotations

from enum import Enum

from sparrow.errors import AllProvidersExhaustedError
from sparrow.routing.health import RouteHealthTracker
from sparrow.routing.modes import fair_order, fast_order, quality_order
from sparrow.routing.quota import QuotaTracker


class RoutingMode(Enum):
    FAIR = "fair"
    FAST = "fast"
    QUALITY = "quality"
    MODEL = "model"


def _coerce_mode(mode: RoutingMode | str) -> RoutingMode:
    if isinstance(mode, RoutingMode):
        return mode
    try:
        return RoutingMode(mode.strip().lower())
    except (AttributeError, ValueError) as error:
        raise ValueError("expected one of: fair, fast, quality, model") from error


class Route:
    def __init__(
        self,
        provider_id: str,
        model_id: str,
        quality: int = 5,
        context_window: int = 128000,
        avg_latency_ms: float = 0.0,
        daily_quota: int | None = None,
    ) -> None:
        if daily_quota is not None and daily_quota < 0:
            raise ValueError("daily_quota must be non-negative")
        self.provider_id = provider_id
        self.model_id = model_id
        self.quality = quality
        self.context_window = context_window
        self.avg_latency_ms = avg_latency_ms
        self.daily_quota = daily_quota


class RoutingEngine:
    def __init__(
        self,
        health_tracker: RouteHealthTracker | None = None,
        quota: QuotaTracker | None = None,
        mode: RoutingMode | str = RoutingMode.FAIR,
    ) -> None:
        self._routes: list[Route] = []
        self._rr_indices: dict[str, int] = {}
        self._health = health_tracker
        self._quota = quota
        self._mode = _coerce_mode(mode)

    @property
    def route_count(self) -> int:
        return len(self._routes)

    @property
    def mode(self) -> RoutingMode:
        return self._mode

    def register_route(self, route: Route) -> None:
        self._routes.append(route)

    def get_candidates(
        self,
        model: str,
        max_tokens: int | None = None,
        provider_id: str | None = None,
    ) -> list[Route]:
        candidates = list(self._routes) if model == "auto" else [r for r in self._routes if r.model_id == model]

        if provider_id is not None:
            candidates = [route for route in candidates if route.provider_id == provider_id]

        candidates = self._healthy_candidates(candidates)

        if self._quota is not None:
            candidates = [
                route
                for route in candidates
                if (
                    self._quota.can_request(route.provider_id, route.model_id, route.daily_quota)
                    if route.daily_quota is not None
                    else self._quota.can_request(route.provider_id, route.model_id)
                )
            ]

        candidates = self._filter_by_context(candidates, max_tokens)

        return candidates

    def ordered_candidates(
        self,
        model: str,
        max_tokens: int | None = None,
        provider_id: str | None = None,
        mode: RoutingMode | str | None = None,
    ) -> list[Route]:
        if provider_id is None:
            if max_tokens is None:
                candidates = self.get_candidates(model)
            else:
                candidates = self.get_candidates(model, max_tokens=max_tokens)
        else:
            candidates = self.get_candidates(model, max_tokens=max_tokens, provider_id=provider_id)

        if not candidates:
            return []

        selected_mode = self._mode if mode is None else _coerce_mode(mode)
        if selected_mode == RoutingMode.FAIR:
            index = self._rr_indices.get(model, 0)
            ordered = fair_order(candidates, index)
            self._rr_indices[model] = index + 1
            return ordered
        if selected_mode == RoutingMode.FAST:
            return fast_order(candidates)
        if selected_mode == RoutingMode.QUALITY:
            return quality_order(candidates)
        return candidates

    def _healthy_candidates(self, candidates: list[Route]) -> list[Route]:
        if self._health is None:
            return candidates
        return [r for r in candidates if self._health.is_healthy(f"{r.provider_id}:{r.model_id}")]

    def _filter_by_context(self, candidates: list[Route], max_tokens: int | None) -> list[Route]:
        if max_tokens is None:
            return candidates
        return [r for r in candidates if r.context_window >= max_tokens]

    def select(
        self,
        model: str,
        mode: RoutingMode | str | None = None,
        max_tokens: int | None = None,
    ) -> Route:
        candidates = self.get_candidates(model, max_tokens=max_tokens)
        if not candidates:
            raise AllProvidersExhaustedError(model)

        selected_mode = self._mode if mode is None else _coerce_mode(mode)
        if selected_mode == RoutingMode.FAIR:
            index = self._rr_indices.get(model, 0)
            route = candidates[index % len(candidates)]
            self._rr_indices[model] = index + 1
            return route
        elif selected_mode == RoutingMode.FAST:
            return min(candidates, key=lambda r: r.avg_latency_ms)
        elif selected_mode == RoutingMode.QUALITY:
            return max(candidates, key=lambda r: r.quality)
        else:
            return candidates[0]
