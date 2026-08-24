from __future__ import annotations

import secrets
import threading

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class APIKeyAuth:
    def __init__(self, key: str | None = None) -> None:
        self._key: str | None = key

    def set_keys(self, key: str | None) -> None:
        self._key = key

    def is_valid(self, raw_key: str | None) -> bool:
        if raw_key is None or self._key is None:
            return False
        return secrets.compare_digest(raw_key, self._key)


_api_key_auth: APIKeyAuth | None = None
_lock = threading.Lock()


def get_api_key_auth() -> APIKeyAuth:
    global _api_key_auth
    if _api_key_auth is None:
        with _lock:
            if _api_key_auth is None:
                _api_key_auth = APIKeyAuth(key=None)
    return _api_key_auth


PUBLIC_PATHS = frozenset({"/", "/healthz", "/readyz"})

PROTECTED_PATHS = frozenset(
    {
        "/v1/chat/completions",
        "/v1/embeddings",
        "/v1/models",
        "/v1/providers",
        "/stats",
        "/metrics",
    }
)


def _authentication_error() -> JSONResponse:
    return JSONResponse(
        {"error": "Authentication required"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _header_api_key(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator:
            return None
        return token.strip() or None
    return request.headers.get("X-API-Key", "").strip() or None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS:
            return await call_next(request)

        if path not in PROTECTED_PATHS:
            return await call_next(request)

        api_key = _header_api_key(request)

        if not api_key:
            return _authentication_error()

        auth = get_api_key_auth()
        if not auth.is_valid(api_key):
            return _authentication_error()

        request.state.api_key = api_key
        return await call_next(request)
