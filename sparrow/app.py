from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from sparrow.adapters.base import ProviderAdapter
from sparrow.adapters.registry import AdapterRegistry
from sparrow.cache import ResponseCache
from sparrow.client import SparrowClient
from sparrow.config.loader import load_all_providers
from sparrow.dashboard import DASHBOARD_HTML
from sparrow.metrics import metrics_endpoint as _metrics_endpoint
from sparrow.metrics import record_cache_hit, record_cache_miss, record_request
from sparrow.middleware.auth import AuthMiddleware, _generate_api_key, get_api_key_auth
from sparrow.middleware.body_limit import BodySizeLimitMiddleware
from sparrow.middleware.logging import StructuredLogger, generate_request_id
from sparrow.middleware.rate_limit import RateLimiterMiddleware
from sparrow.models import ChatRequest, EmbeddingRequest
from sparrow.proxy import WARPProxy
from sparrow.routing.engine import Route as RoutingRoute
from sparrow.routing.engine import RoutingEngine
from sparrow.routing.quota import QuotaTracker
from sparrow.stats import StatsTracker

logger = logging.getLogger("sparrow")

class SecurityHeadersMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        forwarded_proto = request.headers.get("x-forwarded-proto", "http")
        if forwarded_proto == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

_client: SparrowClient | None = None
_routing_engine: RoutingEngine | None = None
_stats: StatsTracker | None = None
_adapter_registry: AdapterRegistry | None = None
_cache: ResponseCache | None = None
_quota: QuotaTracker | None = None
_structured_logger: StructuredLogger | None = None
_start_time: float = 0.0

@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    global _client, _routing_engine, _stats, _adapter_registry, _start_time, _cache, _quota, _structured_logger

    _start_time = time.time()
    _stats = StatsTracker()
    _structured_logger = StructuredLogger()

    _cache = ResponseCache()
    _quota = QuotaTracker()

    warp = WARPProxy()
    _client = SparrowClient(warp_proxy=warp)
    await _client.start()

    _adapter_registry = AdapterRegistry()
    _adapter_registry.set_client(_client.get_client(use_warp=True))

    providers_data = load_all_providers()
    for provider_id, provider_data in providers_data.get("providers", {}).items():
        base_url = provider_data.get("base_url", "")
        provider_name = provider_data.get("name", provider_id)
        models = provider_data.get("models", [])

        if base_url:
            _adapter_registry.register(
                provider_id=provider_id,
                provider_name=provider_name,
                base_url=base_url,
                models=models,
            )

        if _routing_engine is None:
            _routing_engine = RoutingEngine(quota=_quota)

        for model in models:
            if model.get("enabled", True):
                route = RoutingRoute(
                    provider_id=provider_id,
                    model_id=model.get("slug", model.get("id", "")),
                    quality=model.get("quality", 5),
                    context_window=model.get("context", 128000),
                )
                _routing_engine.register_route(route)

    from sparrow.config.loader import load_config

    settings = load_config()
    raw_key = settings.api_key
    if not raw_key:
        default_key = _generate_api_key()
        logger.warning("No SPARROW_API_KEY configured. Dev fallback key: %s", default_key)
        raw_key = default_key
    get_api_key_auth().set_keys(raw_key)

    logger.info(
        "SparroW started: %d providers, %d routes",
        len(_adapter_registry.list_providers()),
        _routing_engine.route_count if _routing_engine else 0,
    )

    yield

    if _client:
        await _client.stop()

async def health_check(request: Request) -> JSONResponse:
    uptime = int(time.time() - _start_time) if _start_time else 0
    warp_status = {}
    if _client:
        warp_status = _client.warp.get_status()
    return JSONResponse({
        "status": "ok",
        "uptime_seconds": uptime,
        "total_routes": _routing_engine.route_count if _routing_engine else 0,
        "providers": len(_adapter_registry.list_providers()) if _adapter_registry else 0,
        **warp_status,
    })

async def list_models(request: Request) -> JSONResponse:
    models = []
    if _adapter_registry:
        for provider_id, adapter in _adapter_registry.get_all().items():
            for model_id in adapter.available_models:
                models.append({
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": provider_id,
                })
    return JSONResponse({"object": "list", "data": models})

async def list_providers(request: Request) -> JSONResponse:
    providers = []
    if _adapter_registry:
        stats_summary = _stats.get_summary() if _stats else {}
        providers_stats = stats_summary.get("providers", {})
        all_usage = _quota.get_all_usage() if _quota else {}

        for provider_id, adapter in _adapter_registry.get_all().items():
            provider_stats = providers_stats.get(provider_id, {})
            provider_quota = {
                k.split(":", 1)[1]: v
                for k, v in all_usage.items()
                if k.startswith(f"{provider_id}:")
            }

            cb_state = "closed"
            if _routing_engine and _routing_engine._health:
                model_ids = adapter.available_models
                if model_ids:
                    breaker = _routing_engine._health.get_breaker(
                        f"{provider_id}:{model_ids[0]}"
                    )
                    cb_state = breaker.state

            providers.append({
                "id": provider_id,
                "name": adapter.name,
                "models": adapter.available_models,
                "available": adapter.is_available(),
                "circuit_breaker_state": cb_state,
                "quota_used_today": provider_quota,
                "avg_latency_ms": provider_stats.get("avg_latency_ms", 0),
                "success_rate": provider_stats.get("success_rate", 0),
            })
    return JSONResponse({"object": "list", "data": providers})

async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    request_id = generate_request_id()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    model_input = body.get("model", "auto")

    max_tokens = body.get("max_tokens")

    if _routing_engine is None:
        return JSONResponse({"error": "Routing engine not initialized"}, status_code=500)

    candidates = _routing_engine.get_candidates(model_input, max_tokens=max_tokens)
    if not candidates:
        return JSONResponse({"error": f"No routes for model: {model_input}"}, status_code=503)

    if _adapter_registry is None:
        return JSONResponse({"error": "Adapter registry not initialized"}, status_code=500)

    chat_req = ChatRequest(**body)
    start = time.time()

    if chat_req.stream:
        for route in candidates:
            adapter = _adapter_registry.get(route.provider_id)
            if adapter is None:
                continue

            chat_req.model = route.model_id
            stream_success = True

            async def _stream_events(
                _adapter: ProviderAdapter,
                _chat_req: ChatRequest,
                _model: str,
                _provider: str,
            ) -> AsyncIterator[str]:
                nonlocal stream_success
                try:
                    async for chunk in _adapter.chat_completion_stream(_chat_req, _model):
                        yield f"data: {chunk}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    stream_success = False
                    logger.error("Stream error from %s: %s", _provider, e)
                    error_chunk = {
                        "error": {"message": str(e), "type": "upstream_error"},
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                finally:
                    latency_ms = (time.time() - start) * 1000
                    if _stats:
                        _stats.record_request(_provider, success=stream_success, latency_ms=latency_ms)
                    record_request(
                        _provider, _model,
                        "success" if stream_success else "error",
                        latency_ms / 1000,
                    )

            try:
                return StreamingResponse(
                    _stream_events(adapter, chat_req, route.model_id, route.provider_id),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "X-Sparrow-Provider": route.provider_id,
                        "X-Sparrow-Model": route.model_id,
                    },
                )
            except Exception:
                logger.warning("Failover: %s/%s failed, trying next", route.provider_id, route.model_id)
                continue

        return JSONResponse(
            {"error": "All providers failed for streaming"},
            status_code=503,
        )

    last_error: Exception | None = None
    for route in candidates:
        adapter = _adapter_registry.get(route.provider_id)
        if adapter is None:
            continue

        if _cache:
            cached = _cache.get(route.provider_id, model_input, body)
            if cached is not None:
                record_cache_hit(route.provider_id)
                return JSONResponse(cached)
            else:
                record_cache_miss(route.provider_id)

        chat_req.model = route.model_id

        try:
            response = await adapter.chat_completion(chat_req, route.model_id)
            latency_ms = (time.time() - start) * 1000
            if _stats:
                tokens = response.usage.total_tokens if response.usage else 0
                _stats.record_request(route.provider_id, success=True, latency_ms=latency_ms, tokens=tokens)
            record_request(route.provider_id, route.model_id, "success", latency_ms / 1000)
            if _quota:
                _quota.record(route.provider_id, route.model_id)

            resp_json = response.model_dump()
            resp_json["x_sparrow_provider"] = route.provider_id
            resp_json["x_sparrow_model"] = route.model_id

            if _cache:
                _cache.set(route.provider_id, model_input, body, resp_json)

            if _structured_logger:
                _structured_logger.log_request(
                    method=request.method,
                    path=request.url.path,
                    status_code=200,
                    duration_ms=latency_ms,
                    request_id=request_id,
                    provider=route.provider_id,
                    model=route.model_id,
                )

            return JSONResponse(resp_json)

        except (httpx.TimeoutException, httpx.HTTPStatusError, Exception) as e:
            latency_ms = (time.time() - start) * 1000
            if _stats:
                _stats.record_request(route.provider_id, success=False, latency_ms=latency_ms)
            record_request(route.provider_id, route.model_id, "error", latency_ms / 1000)
            last_error = e
            if _structured_logger:
                _structured_logger.log_error(
                    message=f"Failover: {route.provider_id}/{route.model_id} failed ({type(e).__name__})",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    provider=route.provider_id,
                )
            logger.warning(
                "Failover: %s/%s failed (%s), trying next",
                route.provider_id, route.model_id, type(e).__name__,
            )
            continue

    if isinstance(last_error, httpx.TimeoutException):
        return JSONResponse({"error": "All providers timed out"}, status_code=504)
    return JSONResponse(
        {"error": f"All providers exhausted for model: {model_input}"},
        status_code=503,
    )

async def stats_endpoint(request: Request) -> JSONResponse:
    if _stats is None:
        return JSONResponse({"error": "Stats not initialized"}, status_code=500)
    return JSONResponse(_stats.get_summary())

async def embeddings(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    model_input = body.get("model", "auto")

    if _routing_engine is None or _adapter_registry is None:
        return JSONResponse({"error": "Routing not initialized"}, status_code=500)

    candidates = _routing_engine.get_candidates(model_input)
    if not candidates:
        return JSONResponse({"error": f"No routes for model: {model_input}"}, status_code=503)

    emb_req = EmbeddingRequest(**body)
    start = time.time()

    for route in candidates:
        adapter = _adapter_registry.get(route.provider_id)
        if adapter is None:
            continue

        try:
            response = await adapter.embedding(emb_req, route.model_id)
            latency_ms = (time.time() - start) * 1000
            if _stats:
                tokens = response.usage.total_tokens if response.usage else 0
                _stats.record_request(route.provider_id, success=True, latency_ms=latency_ms, tokens=tokens)

            resp_json = response.model_dump()
            resp_json["x_sparrow_provider"] = route.provider_id
            resp_json["x_sparrow_model"] = route.model_id
            return JSONResponse(resp_json)

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            if _stats:
                _stats.record_request(route.provider_id, success=False, latency_ms=latency_ms)
            logger.warning(
                "Failover embeddings: %s/%s failed (%s)",
                route.provider_id, route.model_id, type(e).__name__,
            )
            continue

    return JSONResponse(
        {"error": f"All providers exhausted for embeddings model: {model_input}"},
        status_code=503,
    )

async def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)

async def metrics_handler(request: Request) -> Response:
    return _metrics_endpoint()

def create_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/", dashboard, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/v1/embeddings", embeddings, methods=["POST"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/providers", list_providers, methods=["GET"]),
            Route("/healthz", health_check, methods=["GET"]),
            Route("/stats", stats_endpoint, methods=["GET"]),
            Route("/metrics", metrics_handler, methods=["GET"]),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(AuthMiddleware),
            Middleware(SecurityHeadersMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
                allow_credentials=True,
            ),
            Middleware(RateLimiterMiddleware, max_requests=100, window_seconds=60),
            Middleware(BodySizeLimitMiddleware, max_body_size=1_048_576),
        ],
    )
    return app
