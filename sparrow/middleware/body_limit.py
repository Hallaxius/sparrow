from __future__ import annotations

from typing import ClassVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    _ROUTE_LIMITS: ClassVar[dict[str, int]] = {
        "/v1/chat/completions": 1_048_576,
        "/v1/embeddings": 512_000,
    }

    def __init__(self, app: ASGIApp, max_body_size: int = 1_048_576) -> None:
        super().__init__(app)
        self._max_body_size = max_body_size

    def _get_limit(self, path: str) -> int:
        return self._ROUTE_LIMITS.get(path, self._max_body_size)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("GET", "HEAD", "DELETE", "OPTIONS"):
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                return JSONResponse(
                    {"error": "Invalid Content-Length header"},
                    status_code=400,
                )
            limit = self._get_limit(request.url.path)
            if size > limit:
                return JSONResponse(
                    {"error": "Request body too large", "max_bytes": limit},
                    status_code=413,
                )

        return await call_next(request)
