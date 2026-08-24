from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "sparrow_requests_total",
    "Total number of requests",
    ["provider", "model", "status"],
)

REQUEST_DURATION = Histogram(
    "sparrow_request_duration_seconds",
    "Request duration in seconds",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

CACHE_HITS = Counter(
    "sparrow_cache_hits_total",
    "Total cache hits",
    ["provider"],
)

CACHE_MISSES = Counter(
    "sparrow_cache_misses_total",
    "Total cache misses",
    ["provider"],
)

ACTIVE_CONNECTIONS = Counter(
    "sparrow_active_connections",
    "Number of active connections",
)


def record_request(provider: str, model: str, status: str, duration_seconds: float) -> None:
    REQUEST_COUNT.labels(provider=provider, model=model, status=status).inc()
    REQUEST_DURATION.labels(provider=provider, model=model).observe(duration_seconds)


def record_cache_hit(provider: str) -> None:
    CACHE_HITS.labels(provider=provider).inc()


def record_cache_miss(provider: str) -> None:
    CACHE_MISSES.labels(provider=provider).inc()


def metrics_endpoint() -> Response:
    return Response(
        generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
