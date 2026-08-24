from __future__ import annotations

from typing import TYPE_CHECKING

from sparrow.routing.health import RouteHealthTracker

if TYPE_CHECKING:
    from sparrow.routing.engine import Route


def fair_select(routes: list[Route], index: int) -> Route:
    return routes[index % len(routes)]


def fair_order(routes: list[Route], index: int) -> list[Route]:
    if not routes:
        return []
    offset = index % len(routes)
    return routes[offset:] + routes[:offset]


def fast_order(routes: list[Route]) -> list[Route]:
    return sorted(routes, key=lambda route: route.avg_latency_ms)


def quality_order(routes: list[Route]) -> list[Route]:
    return sorted(routes, key=lambda route: route.quality, reverse=True)


def quality_select(
    routes: list[Route],
    health: RouteHealthTracker | None = None,
) -> Route:
    if health:
        healthy = [r for r in routes if health.is_healthy(f"{r.provider_id}:{r.model_id}")]
        if healthy:
            routes = healthy
    return max(routes, key=lambda r: r.quality)
