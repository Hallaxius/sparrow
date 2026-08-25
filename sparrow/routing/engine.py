from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from sparrow.errors import AllProvidersExhaustedError
from sparrow.routing.context_window import ContextWindowLearner
from sparrow.routing.health import RouteHealthTracker
from sparrow.routing.modes import fair_order, fast_order, quality_order
from sparrow.routing.quota import QuotaTracker

if TYPE_CHECKING:
    from sparrow.routing.capability import CapabilityScorer


class RoutingMode(Enum):
    FAIR = "fair"
    FAST = "fast"
    QUALITY = "quality"
    MODEL = "model"
    TASK = "task"


def _coerce_mode(mode: RoutingMode | str) -> RoutingMode:
    if isinstance(mode, RoutingMode):
        return mode
    try:
        return RoutingMode(mode.strip().lower())
    except (AttributeError, ValueError) as error:
        raise ValueError("expected one of: fair, fast, quality, model, task") from error


def _build_related_models(model_groups: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    related: dict[str, set[str]] = {}
    for members in model_groups.values():
        group = {member for member in members if member}
        if len(group) < 2:
            continue
        for member in group:
            related.setdefault(member, set()).update(group)
    return {member: frozenset(peers) for member, peers in related.items()}


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
        model_groups: dict[str, list[str]] | None = None,
        capability_scorer: CapabilityScorer | None = None,
        context_learner: ContextWindowLearner | None = None,
    ) -> None:
        self._routes: list[Route] = []
        self._rr_indices: dict[str, int] = {}
        self._health = health_tracker
        self._quota = quota
        self._mode = _coerce_mode(mode)
        self._related_models = _build_related_models(model_groups or {})
        self._capability_scorer = capability_scorer
        self._context_learner = context_learner

    @property
    def route_count(self) -> int:
        return len(self._routes)

    @property
    def mode(self) -> RoutingMode:
        return self._mode

    def register_route(self, route: Route) -> None:
        self._routes.append(route)

    def record_context_overflow(
        self, provider_id: str, model_id: str, error_message: str, max_tokens: int | None = None
    ) -> bool:
        if self._context_learner is None:
            return False
        return self._context_learner.record_from_error(provider_id, model_id, error_message, max_tokens)

    def get_candidates(
        self,
        model: str,
        max_tokens: int | None = None,
        provider_id: str | None = None,
    ) -> list[Route]:
        if model == "auto":
            candidates = list(self._routes)
        else:
            related = self._related_models.get(model)
            if related is None:
                candidates = [r for r in self._routes if r.model_id == model]
            else:
                candidates = [r for r in self._routes if r.model_id in related]

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
        messages: list[dict[str, Any]] | None = None,
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
        if selected_mode == RoutingMode.TASK and messages and self._capability_scorer is not None:
            return self._task_order(candidates, messages)
        return candidates

    def _task_order(self, candidates: list[Route], messages: list[dict[str, Any]]) -> list[Route]:
        if not self._capability_scorer:
            return candidates
        hint = self._capability_scorer.detect_task(messages)
        context_tokens = self._capability_scorer._estimate_tokens(messages)
        model_ids = {c.model_id for c in candidates}
        scored = self._capability_scorer.score_models(hint.task_type, context_tokens, list(model_ids))
        score_map = {s.model_id: s.total for s in scored}
        ranked = sorted(candidates, key=lambda r: score_map.get(r.model_id, 0.0), reverse=True)
        return ranked

    def _healthy_candidates(self, candidates: list[Route]) -> list[Route]:
        if self._health is None:
            return candidates
        return [r for r in candidates if self._health.is_healthy(f"{r.provider_id}:{r.model_id}")]

    def _filter_by_context(self, candidates: list[Route], max_tokens: int | None) -> list[Route]:
        if max_tokens is None:
            return candidates
        if self._context_learner is not None:
            return [
                r
                for r in candidates
                if self._context_learner.get_effective_limit(r.provider_id, r.model_id, r.context_window)
                >= max_tokens
            ]
        return [r for r in candidates if r.context_window >= max_tokens]

    def select(
        self,
        model: str,
        mode: RoutingMode | str | None = None,
        max_tokens: int | None = None,
        messages: list[dict[str, Any]] | None = None,
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
        if selected_mode == RoutingMode.FAST:
            return min(candidates, key=lambda r: r.avg_latency_ms)
        if selected_mode == RoutingMode.QUALITY:
            return max(candidates, key=lambda r: r.quality)
        if selected_mode == RoutingMode.TASK and messages and self._capability_scorer is not None:
            ordered = self._task_order(candidates, messages)
            return ordered[0]
        return candidates[0]
