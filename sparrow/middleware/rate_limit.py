from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class RateLimiter:

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self._window
        ]
        if len(self._requests[client_ip]) >= self._max_requests:
            return False
        self._requests[client_ip].append(now)
        return True

class RateLimiterMiddleware(BaseHTTPMiddleware):

    def __init__(self, app: ASGIApp, max_requests: int = 100, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._limiter = RateLimiter(max_requests, window_seconds)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else "unknown"
        if not self._limiter.is_allowed(client_ip):
            return JSONResponse(
                {"error": "Rate limit exceeded", "retry_after": self._limiter._window},
                status_code=429,
                headers={"Retry-After": str(self._limiter._window)},
            )
        return await call_next(request)
