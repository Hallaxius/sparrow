from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _generate_api_key() -> str:
    return f"sk-{secrets.token_hex(24)}"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class APIKeyInfo:
    key_hash: str
    name: str
    created_at: float = field(default_factory=time.time)
    last_used: float = 0.0
    request_count: int = 0
    rate_limit: int = 100
    rate_window: int = 60
    enabled: bool = True
    expires_at: float | None = None


@dataclass
class RateLimitState:
    requests: list[float] = field(default_factory=list)


class APIKeyStore:

    def __init__(self) -> None:
        self._keys: dict[str, APIKeyInfo] = {}
        self._rate_states: dict[str, RateLimitState] = defaultdict(RateLimitState)

    def create_key(self, name: str, rate_limit: int = 100, rate_window: int = 60) -> str:
        raw_key = _generate_api_key()
        key_hash = _hash_key(raw_key)
        self._keys[key_hash] = APIKeyInfo(
            key_hash=key_hash,
            name=name,
            rate_limit=rate_limit,
            rate_window=rate_window,
        )
        return raw_key

    def validate_key(self, raw_key: str) -> APIKeyInfo | None:
        key_hash = _hash_key(raw_key)
        info = self._keys.get(key_hash)
        if info is None or not info.enabled:
            return None

        info.last_used = time.time()
        info.request_count += 1
        return info

    def validate_and_check_rate(self, raw_key: str) -> tuple[APIKeyInfo | None, bool, float]:
        key_hash = _hash_key(raw_key)
        info = self._keys.get(key_hash)
        if info is None or not info.enabled:
            return None, True, 60.0
        info.last_used = time.time()
        info.request_count += 1
        is_limited, retry_after = self._check_rate(info)
        return info, is_limited, retry_after

    def _check_rate(self, info: APIKeyInfo) -> tuple[bool, float]:
        key_hash = info.key_hash
        state = self._rate_states[key_hash]
        now = time.time()
        state.requests = [t for t in state.requests if now - t < info.rate_window]

        if len(state.requests) >= info.rate_limit:
            oldest = state.requests[0] if state.requests else now
            retry_after = info.rate_window - (now - oldest)
            return True, max(retry_after, 1.0)

        state.requests.append(now)
        return False, 0.0

    def record_usage(self, key_hash: str) -> None:
        if key_hash in self._keys:
            info = self._keys[key_hash]
            info.last_used = time.time()
            info.request_count += 1

    def is_rate_limited(self, key_hash: str) -> tuple[bool, float]:
        info = self._keys.get(key_hash)
        if info is None:
            return True, 60.0
        return self._check_rate(info)

    def is_expired(self, info: APIKeyInfo) -> bool:
        if info.expires_at is None:
            return False
        return time.time() > info.expires_at

    def cleanup_expired(self) -> int:
        now = time.time()
        expired_hashes = [
            h for h, info in self._keys.items()
            if info.expires_at is not None and now > info.expires_at
        ]
        for h in expired_hashes:
            del self._keys[h]
            self._rate_states.pop(h, None)
        return len(expired_hashes)

    def list_keys(self) -> list[dict[str, object]]:
        result = []
        for info in self._keys.values():
            result.append({
                "name": info.name,
                "created_at": info.created_at,
                "last_used": info.last_used,
                "request_count": info.request_count,
                "rate_limit": info.rate_limit,
                "enabled": info.enabled,
                "expires_at": info.expires_at,
            })
        return result

    def delete_key(self, key_hash: str) -> bool:
        if key_hash in self._keys:
            del self._keys[key_hash]
            self._rate_states.pop(key_hash, None)
            return True
        return False

    def disable_key(self, key_hash: str) -> bool:
        info = self._keys.get(key_hash)
        if info:
            info.enabled = False
            return True
        return False

    def get_key(self, key_hash: str) -> APIKeyInfo | None:
        return self._keys.get(key_hash)


_api_key_store: APIKeyStore | None = None
_lock = threading.Lock()


def get_api_key_store() -> APIKeyStore:
    global _api_key_store
    if _api_key_store is None:
        with _lock:
            if _api_key_store is None:
                _api_key_store = APIKeyStore()
    return _api_key_store


PUBLIC_PATHS = frozenset({"/healthz"})

PROTECTED_PATHS = frozenset({"/v1/chat/completions"})


async def manage_api_keys(request: Request) -> Response:
    store = get_api_key_store()

    if request.method == "GET":
        return JSONResponse(store.list_keys())

    if request.method == "POST":
        body = await request.json()
        name = body.get("name", "unnamed")
        rate_limit = min(int(body.get("rate_limit", 100)), 10000)
        rate_window = max(int(body.get("rate_window", 60)), 1)
        expires_at = body.get("expires_at")
        if expires_at is not None:
            expires_at = float(expires_at)
        raw_key = store.create_key(name, rate_limit, rate_window)
        if expires_at is not None:
            key_hash = _hash_key(raw_key)
            store._keys[key_hash].expires_at = expires_at
        return JSONResponse(
            {
                "key": raw_key,
                "name": name,
                "rate_limit": rate_limit,
                "rate_window": rate_window,
                "expires_at": expires_at,
            },
            status_code=201,
        )

    return JSONResponse({"error": "Method not allowed"}, status_code=405)


async def manage_single_api_key(request: Request) -> Response:
    store = get_api_key_store()
    key_hash = request.path_params["key_hash"]

    if request.method == "DELETE":
        if store.delete_key(key_hash):
            return JSONResponse({"deleted": True})
        return JSONResponse({"error": "Key not found"}, status_code=404)

    if request.method == "PATCH":
        body = await request.json()
        info = store.get_key(key_hash)
        if info is None:
            return JSONResponse({"error": "Key not found"}, status_code=404)
        if "name" in body:
            info.name = body["name"]
        if "rate_limit" in body:
            info.rate_limit = min(int(body["rate_limit"]), 10000)
        if "rate_window" in body:
            info.rate_window = max(int(body["rate_window"]), 1)
        if "enabled" in body:
            info.enabled = bool(body["enabled"])
        if "expires_at" in body:
            info.expires_at = float(body["expires_at"]) if body["expires_at"] is not None else None
        return JSONResponse({
            "name": info.name,
            "rate_limit": info.rate_limit,
            "rate_window": info.rate_window,
            "enabled": info.enabled,
            "expires_at": info.expires_at,
        })

    return JSONResponse({"error": "Method not allowed"}, status_code=405)


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS:
            return await call_next(request)

        if path not in PROTECTED_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        api_key = None

        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()

        if not api_key:
            api_key = request.headers.get("X-API-Key", "")

        if not api_key:
            return JSONResponse(
                {"error": "Missing API key. Provide via Authorization: Bearer sk-... or X-API-Key header."},
                status_code=401,
            )

        store = get_api_key_store()
        key_info, is_limited, retry_after = store.validate_and_check_rate(api_key)
        if key_info is None:
            return JSONResponse(
                {"error": "Invalid API key"},
                status_code=401,
            )

        if is_limited:
            return JSONResponse(
                {"error": "Rate limit exceeded", "retry_after": round(retry_after, 1)},
                status_code=429,
                headers={"Retry-After": str(int(retry_after))},
            )

        request.state.api_key = key_info
        return await call_next(request)
