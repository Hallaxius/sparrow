from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sparrow.routing.engine import Route


def fair_order(routes: list[Route], index: int) -> list[Route]:
    if not routes:
        return []
    offset = index % len(routes)
    return routes[offset:] + routes[:offset]


def fast_order(routes: list[Route]) -> list[Route]:
    return sorted(routes, key=lambda route: route.avg_latency_ms)


def quality_order(routes: list[Route]) -> list[Route]:
    return sorted(routes, key=lambda route: route.quality, reverse=True)
