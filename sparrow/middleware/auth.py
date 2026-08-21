from __future__ import annotations

import json
import secrets
import threading

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _generate_api_key() -> str:
    return f"sk-{secrets.token_hex(24)}"


class APIKeyAuth:

    def __init__(self, key: str | None = None) -> None:
        self._key: str | None = key

    def set_keys(self, key: str | None) -> None:
        self._key = key

    def is_valid(self, raw_key: str | None) -> bool:
        return raw_key is not None and self._key is not None and raw_key == self._key


_api_key_auth: APIKeyAuth | None = None
_lock = threading.Lock()


def get_api_key_auth() -> APIKeyAuth:
    global _api_key_auth
    if _api_key_auth is None:
        with _lock:
            if _api_key_auth is None:
                _api_key_auth = APIKeyAuth(key=None)
    return _api_key_auth


PUBLIC_PATHS = frozenset({"/healthz", "/metrics"})

PROTECTED_PATHS = frozenset({"/v1/chat/completions", "/v1/embeddings"})


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS:
            return await call_next(request)

        if path not in PROTECTED_PATHS:
            return await call_next(request)

        api_key: str | None = None

        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            try:
                body = await request.body()
                if body:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        api_key = parsed.get("api_key")
            except (json.JSONDecodeError, ValueError):
                pass

        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip() or None

        if not api_key:
            api_key = request.headers.get("X-API-Key") or None

        if not api_key:
            return JSONResponse(
                {
                    "error": "API key required",
                    "message": "Provide 'api_key' in the request body or an Authorization/X-API-Key header",
                },
                status_code=401,
            )

        auth = get_api_key_auth()
        if not auth.is_valid(api_key):
            return JSONResponse(
                {"error": "Invalid API key"},
                status_code=401,
            )

        request.state.api_key = api_key
        return await call_next(request)
