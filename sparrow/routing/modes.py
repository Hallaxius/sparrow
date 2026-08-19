from __future__ import annotations

from sparrow.routing.engine import Route
from sparrow.routing.health import RouteHealthTracker


def fair_select(routes: list[Route], index: int) -> Route:
    return routes[index % len(routes)]


def quality_select(
    routes: list[Route],
    health: RouteHealthTracker | None = None,
) -> Route:
    if health:
        healthy = [r for r in routes if health.is_healthy(f"{r.provider_id}:{r.model_id}")]
        if healthy:
            routes = healthy
    return max(routes, key=lambda r: r.quality)
