from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from sparrow.adapters.anthropic_shim import (
    _sse_event,
    anthropic_to_chat_request,
    chat_response_to_anthropic,
    create_anthropic_error,
    openai_chunk_to_anthropic_sse,
)
from sparrow.adapters.base import ProviderAdapter
from sparrow.adapters.registry import AdapterRegistry
from sparrow.client import SparrowClient
from sparrow.config.aliases import AliasResolutionError, AliasResolver
from sparrow.config.loader import load_all_providers, load_config
from sparrow.config.models import Settings
from sparrow.dashboard import DASHBOARD_HTML
from sparrow.errors import ConfigError
from sparrow.mcp_server import MCPServer
from sparrow.metrics import metrics_endpoint as _metrics_endpoint
from sparrow.metrics import record_request
from sparrow.middleware.auth import AuthMiddleware, get_api_key_auth
from sparrow.middleware.body_limit import BodySizeLimitMiddleware
from sparrow.middleware.logging import StructuredLogger, generate_request_id
from sparrow.models import ChatRequest, ChatResponse, EmbeddingRequest, EmbeddingResponse
from sparrow.plugins.registry import PluginRegistry
from sparrow.proxy import WARPConfig, WARPProxy
from sparrow.routing.capability import CapabilityScorer
from sparrow.routing.context_window import ContextWindowLearner
from sparrow.routing.engine import Route as RoutingRoute
from sparrow.routing.engine import RoutingEngine, RoutingMode
from sparrow.routing.health import CircuitBreaker, RouteHealthTracker
from sparrow.routing.quota import QuotaTracker
from sparrow.routing.rpm import RpmGovernor
from sparrow.stats import StatsTracker

logger = logging.getLogger("sparrow")

_MAX_REQUEST_ATTEMPTS = 4
_MAX_ROUTE_ATTEMPTS = 2
_REQUEST_DEADLINE_SECONDS = 120.0
_STREAM_IDLE_TIMEOUT_SECONDS = 120.0
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 10.0
_RETRY_BACKOFF_FACTOR = 2.0


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        forwarded_proto = request.headers.get("x-forwarded-proto", "http")
        if forwarded_proto == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


_client: SparrowClient | None = None
_routing_engine: RoutingEngine | None = None
_alias_resolver: AliasResolver | None = None
_stats: StatsTracker | None = None
_adapter_registry: AdapterRegistry | None = None
_quota: QuotaTracker | None = None
_rpm_governor: RpmGovernor | None = None
_health: RouteHealthTracker | None = None
_structured_logger: StructuredLogger | None = None
_capability_scorer: CapabilityScorer | None = None
_context_learner: ContextWindowLearner | None = None
_mcp_server: MCPServer | None = None
_plugin_registry: PluginRegistry | None = None
_start_time: float = 0.0


@dataclass(frozen=True, slots=True)
class ReadinessState:
    ready: bool
    reason: str


_readiness = ReadinessState(ready=False, reason="startup_incomplete")


@runtime_checkable
class _ClosableAsyncIterator(Protocol):
    async def aclose(self) -> None: ...


async def _close_stream(stream: AsyncIterator[str]) -> None:
    if isinstance(stream, _ClosableAsyncIterator):
        try:
            await stream.aclose()
        except Exception as error:
            logger.warning("Failed to close upstream stream (%s)", type(error).__name__)


def _compute_readiness() -> ReadinessState:
    if (
        _client is None
        or _routing_engine is None
        or _stats is None
        or _adapter_registry is None
        or _quota is None
        or _health is None
        or _structured_logger is None
    ):
        return ReadinessState(ready=False, reason="startup_incomplete")

    if _routing_engine.route_count == 0:
        return ReadinessState(ready=False, reason="no_routes")

    if _client is None or not _client.warp.is_warp_available():
        return ReadinessState(ready=False, reason="warp_unavailable")

    return ReadinessState(ready=True, reason="ready")


def _readiness_payload() -> dict[str, object]:
    routes = _routing_engine.route_count if _routing_engine else 0
    providers = len(_adapter_registry.list_providers()) if _adapter_registry else 0
    warp_status: dict[str, object] = {
        "warp_enabled": True,
        "warp_available": False,
    }
    if _client:
        warp_status = _client.warp.get_status()

    return {
        "status": "ready" if _readiness.ready else "not_ready",
        "ready": _readiness.ready,
        "reason": _readiness.reason,
        "routes": routes,
        "providers": providers,
        **warp_status,
    }


def _invalid_request_response(
    message: str,
    param: str | None = None,
    code: str | None = None,
) -> JSONResponse:
    error: dict[str, str] = {
        "message": message,
        "type": "invalid_request_error",
    }
    if param:
        error["param"] = param
    if code:
        error["code"] = code
    return JSONResponse({"error": error}, status_code=400)


def _validation_error_response(error: ValidationError) -> JSONResponse:
    details = error.errors(include_input=False)
    if not details:
        return _invalid_request_response("Invalid request", code="invalid_request")

    first = details[0]
    param = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "Invalid request"))
    code = str(first.get("type", "invalid_request"))
    return _invalid_request_response(message, param or None, code)


def _metadata_headers(provider_id: str, model_id: str) -> dict[str, str]:
    return {
        "X-Sparrow-Provider": provider_id,
        "X-Sparrow-Model": model_id,
    }


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


async def _call_with_deadline[T](operation: Callable[[], Awaitable[T]], deadline: float) -> T:
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise TimeoutError("request deadline exceeded")
    async with asyncio.timeout(remaining):
        return await operation()


async def _next_stream_chunk(stream: AsyncIterator[str]) -> str:
    async with asyncio.timeout(_STREAM_IDLE_TIMEOUT_SECONDS):
        return await stream.__anext__()


def _error_status(error: Exception) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    return None


def _is_timeout_error(error: Exception) -> bool:
    return isinstance(error, (httpx.TimeoutException, TimeoutError))


def _is_retryable_error(error: Exception) -> bool:
    status = _error_status(error)
    if status is not None:
        return status in {408, 429} or 500 <= status <= 599
    return isinstance(error, (httpx.TransportError, TimeoutError))


def _attempt_status(error: Exception | None) -> str:
    if error is None:
        return "success"
    if _is_timeout_error(error):
        return "timeout"
    status = _error_status(error)
    if status == 429:
        return "rate_limit"
    if status is not None and 400 <= status <= 499:
        return "client_error"
    if status is not None and status >= 500:
        return "upstream_error"
    if isinstance(error, httpx.TransportError):
        return "transport_error"
    return "error"


def _retry_after_seconds(error: Exception) -> float | None:
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    value = error.response.headers.get("retry-after")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if delay < 0 or not math.isfinite(delay):
        return None
    return delay


def _retry_delay(error: Exception, retry_index: int, deadline: float) -> float:
    retry_after = _retry_after_seconds(error)
    delay = retry_after if retry_after is not None else _RETRY_BASE_DELAY * (_RETRY_BACKOFF_FACTOR**retry_index)
    return min(delay, _RETRY_MAX_DELAY, _remaining(deadline))


async def _wait_before_retry(error: Exception, retry_index: int, deadline: float) -> bool:
    if _remaining(deadline) <= 0:
        return False
    delay = _retry_delay(error, retry_index, deadline)
    if delay > 0:
        await asyncio.sleep(delay)
    return _remaining(deadline) > 0


def _failure_status(last_error: Exception | None, only_timeouts: bool, deadline_expired: bool) -> int:
    if deadline_expired or (last_error is not None and only_timeouts and _is_timeout_error(last_error)):
        return 504
    return 503


def _stream_error_message(error: Exception) -> str:
    return "Upstream stream timed out" if _is_timeout_error(error) else "Upstream stream failed"


def _stream_error_payload(error: Exception) -> dict[str, dict[str, str]]:
    return {"error": {"message": _stream_error_message(error), "type": "upstream_error"}}


def _stream_error_status(error: Exception) -> int:
    return 504 if _is_timeout_error(error) else 502


async def _chat_operation(adapter: ProviderAdapter, request: ChatRequest, model: str) -> ChatResponse:
    return await adapter.chat_completion(request, model)


async def _embedding_operation(adapter: ProviderAdapter, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
    return await adapter.embedding(request, model)


def _route_breaker(route: RoutingRoute) -> CircuitBreaker | None:
    if _routing_engine is None or _routing_engine._health is None:
        return None
    return _routing_engine._health.get_breaker(f"{route.provider_id}:{route.model_id}")


def _acquire_attempt(route: RoutingRoute) -> bool:
    if _quota is None:
        return True
    return _quota.try_acquire(route.provider_id, route.model_id, limit=route.daily_quota)


def _record_attempt(
    route: RoutingRoute,
    started: float,
    error: Exception | None = None,
    tokens: int = 0,
    max_tokens: int | None = None,
) -> float:
    latency_ms = (time.monotonic() - started) * 1000
    success = error is None
    if _stats:
        _stats.record_request(route.provider_id, success=success, latency_ms=latency_ms, tokens=tokens)
    record_request(route.provider_id, route.model_id, _attempt_status(error), latency_ms / 1000)
    breaker = _route_breaker(route)
    if breaker:
        if success:
            breaker.record_success()
        else:
            breaker.record_failure()
    if error is not None and _routing_engine is not None:
        _routing_engine.record_context_overflow(
            route.provider_id, route.model_id, str(error), max_tokens, route.context_window
        )
    return latency_ms


def _get_routing_candidates(
    model: str,
    max_tokens: int | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> list[RoutingRoute]:
    if _routing_engine is None or _alias_resolver is None:
        raise RuntimeError("Routing is not initialized")

    target = _alias_resolver.resolve(model)
    if target.provider_id is None:
        return _routing_engine.ordered_candidates(target.model_id, max_tokens=max_tokens, messages=messages)
    return _routing_engine.ordered_candidates(
        target.model_id,
        max_tokens=max_tokens,
        provider_id=target.provider_id,
        messages=messages,
    )


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    global \
        _client, \
        _routing_engine, \
        _alias_resolver, \
        _stats, \
        _adapter_registry, \
        _start_time, \
        _quota, \
        _health, \
        _structured_logger, \
        _capability_scorer, \
        _context_learner, \
        _mcp_server, \
        _plugin_registry, \
        _rpm_governor, \
        _readiness

    _client = None
    _routing_engine = None
    _alias_resolver = None
    _stats = None
    _adapter_registry = None
    _quota = None
    _rpm_governor = None
    _health = None
    _structured_logger = None
    _capability_scorer = None
    _context_learner = None
    _mcp_server = None
    _plugin_registry = None
    _start_time = time.time()
    _readiness = ReadinessState(ready=False, reason="startup_incomplete")
    startup_failed = False
    yielded = False

    try:
        settings = load_config()
        _stats = StatsTracker()
        _structured_logger = StructuredLogger()
        _quota = QuotaTracker()
        _rpm_governor = RpmGovernor()
        _health = RouteHealthTracker(persist_path=".sparrow/circuit_breakers.json")
        _capability_scorer = CapabilityScorer()
        _context_learner = ContextWindowLearner(persist_path=".sparrow/context_limits.json")

        providers_data = load_all_providers()
        _routing_engine = RoutingEngine(
            health_tracker=_health,
            quota=_quota,
            mode=RoutingMode(settings.routing),
            model_groups=providers_data.get("model_groups", {}),
            capability_scorer=_capability_scorer,
            context_learner=_context_learner,
        )
        warp = WARPProxy(WARPConfig.from_settings(settings))
        _client = SparrowClient(warp_proxy=warp)
        await _client.start()

        _adapter_registry = AdapterRegistry()
        _adapter_registry.set_client(_client.get_client(use_warp=True))

        _alias_resolver = AliasResolver(
            aliases=providers_data.get("aliases", {}),
            provider_models={
                provider_id: {model["slug"] for model in provider_data.get("models", []) if model.get("enabled", True)}
                for provider_id, provider_data in providers_data.get("providers", {}).items()
            },
        )
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
                    api_keys=provider_data.get("api_keys"),
                )

            for model in models:
                if model.get("enabled", True):
                    route = RoutingRoute(
                        provider_id=provider_id,
                        model_id=model.get("slug", model.get("id", "")),
                        quality=model.get("quality", 5),
                        context_window=model.get("context", 128000),
                        daily_quota=provider_data.get("daily_quota"),
                    )
                    _routing_engine.register_route(route)

        _mcp_server = MCPServer(
            stats_tracker=_stats,
            health_tracker=_health,
            routing_engine=_routing_engine,
        )

        _plugin_registry = PluginRegistry()
        await _plugin_registry.startup()

        raw_key = settings.api_key
        if not raw_key:
            raise ConfigError("SPARROW_API_KEY is required; set it in the environment")
        get_api_key_auth().set_keys(raw_key)

        _readiness = _compute_readiness()
        logger.info(
            "SparroW started: %d providers, %d routes",
            len(_adapter_registry.list_providers()),
            _routing_engine.route_count,
        )

        yielded = True
        yield
    except BaseException:
        if not yielded:
            startup_failed = True
            _readiness = ReadinessState(ready=False, reason="startup_failed")
        raise
    finally:
        plugin_registry = _plugin_registry
        if plugin_registry is not None:
            try:
                await plugin_registry.shutdown()
            except BaseException:
                logger.exception("Failed to shut down plugin registry")
        active_client = _client
        try:
            if active_client:
                try:
                    await active_client.stop()
                except BaseException:
                    logger.exception("Failed to close Sparrow client")
        finally:
            _client = None
            _adapter_registry = None
            _routing_engine = None
            _alias_resolver = None
            _health = None
            _quota = None
            _rpm_governor = None
            _structured_logger = None
            _capability_scorer = None
            _context_learner = None
            _mcp_server = None
            _plugin_registry = None
            _stats = None
            _start_time = 0.0
            get_api_key_auth().set_keys(None)
            if not startup_failed:
                _readiness = ReadinessState(ready=False, reason="shutdown")


async def health_check(request: Request) -> JSONResponse:
    uptime = int(time.time() - _start_time) if _start_time else 0
    warp_status = {}
    if _client:
        warp_status = _client.warp.get_status()
    return JSONResponse(
        {
            "status": "ok",
            "uptime_seconds": uptime,
            "total_routes": _routing_engine.route_count if _routing_engine else 0,
            "providers": len(_adapter_registry.list_providers()) if _adapter_registry else 0,
            **warp_status,
        }
    )


async def ready_check(request: Request) -> JSONResponse:
    status_code = 200 if _readiness.ready else 503
    return JSONResponse(_readiness_payload(), status_code=status_code)


async def list_models(request: Request) -> JSONResponse:
    models = []
    if _adapter_registry:
        for provider_id, adapter in _adapter_registry.get_all().items():
            for model_id in adapter.available_models:
                models.append(
                    {
                        "id": model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": provider_id,
                    }
                )
    return JSONResponse({"object": "list", "data": models})


async def list_providers(request: Request) -> JSONResponse:
    providers = []
    if _adapter_registry:
        stats_summary = _stats.get_summary() if _stats else {}
        providers_stats = stats_summary.get("providers", {})
        all_usage = _quota.get_all_usage() if _quota else {}

        for provider_id, adapter in _adapter_registry.get_all().items():
            provider_stats = providers_stats.get(provider_id, {})
            provider_quota = {k.split(":", 1)[1]: v for k, v in all_usage.items() if k.startswith(f"{provider_id}:")}

            cb_state = "closed"
            if _routing_engine and _routing_engine._health:
                model_ids = adapter.available_models
                if model_ids:
                    breaker = _routing_engine._health.get_breaker(f"{provider_id}:{model_ids[0]}")
                    cb_state = breaker.state

            providers.append(
                {
                    "id": provider_id,
                    "name": adapter.name,
                    "models": adapter.available_models,
                    "available": adapter.is_available(),
                    "circuit_breaker_state": cb_state,
                    "quota_used_today": provider_quota,
                    "avg_latency_ms": provider_stats.get("avg_latency_ms", 0),
                    "success_rate": provider_stats.get("success_rate", 0),
                }
            )
    return JSONResponse({"object": "list", "data": providers})


async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    request_id = generate_request_id()

    try:
        body = await request.json()
    except Exception:
        return _invalid_request_response("Invalid JSON body", code="invalid_json")

    registry = _plugin_registry
    if registry is not None:
        body = await registry.run_request_hooks(body)

    try:
        chat_req = ChatRequest.model_validate(body)
    except ValidationError as error:
        return _validation_error_response(error)

    model_input = chat_req.model
    max_tokens = chat_req.max_tokens

    if _routing_engine is None or _alias_resolver is None:
        return JSONResponse({"error": "Routing engine not initialized"}, status_code=500)

    try:
        candidates = _get_routing_candidates(model_input, max_tokens=max_tokens, messages=[m.model_dump() for m in chat_req.messages])
    except AliasResolutionError as error:
        return _invalid_request_response(str(error), param="model", code="invalid_model")
    if not candidates:
        if _routing_engine is not None:
            logger.warning(
                "No routes for model %s: %s",
                model_input,
                _routing_engine.explain_empty_candidates(model_input, max_tokens=max_tokens),
            )
        return JSONResponse({"error": f"No routes for model: {model_input}"}, status_code=503)

    if _adapter_registry is None:
        return JSONResponse({"error": "Adapter registry not initialized"}, status_code=500)

    if chat_req.stream:
        attempts = 0
        stream_last_error: Exception | None = None
        stream_only_timeouts = True
        stream_deadline_expired = False

        for route in candidates:
            if _rpm_governor is not None:
                await _rpm_governor.acquire(route.provider_id)
            deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
            if attempts >= _MAX_REQUEST_ATTEMPTS:
                break
            if _remaining(deadline) <= 0:
                stream_deadline_expired = True
                break

            adapter = _adapter_registry.get(route.provider_id)
            if adapter is None:
                continue

            chat_req.model = route.model_id
            first_chunk: str | None = None
            remaining_gen: AsyncIterator[str] | None = None
            attempt_started = 0.0
            route_error: Exception | None = None

            for route_attempt in range(_MAX_ROUTE_ATTEMPTS):
                if attempts >= _MAX_REQUEST_ATTEMPTS:
                    break
                if _remaining(deadline) <= 0:
                    stream_deadline_expired = True
                    break
                if not _acquire_attempt(route):
                    continue

                attempts += 1
                attempt_started = time.monotonic()
                gen: AsyncIterator[str] | None = None
                stream_started = False
                try:
                    gen = adapter.chat_completion_stream(chat_req, route.model_id)
                    first_chunk = await _call_with_deadline(gen.__anext__, deadline)
                    remaining_gen = gen
                    stream_started = True
                    break
                except Exception as error:
                    route_error = error
                    stream_last_error = error
                    stream_only_timeouts = stream_only_timeouts and _is_timeout_error(error)
                    _record_attempt(route, attempt_started, error, max_tokens=max_tokens)
                    if not (
                        _is_retryable_error(error)
                        and route_attempt + 1 < _MAX_ROUTE_ATTEMPTS
                        and attempts < _MAX_REQUEST_ATTEMPTS
                        and _remaining(deadline) > 0
                    ):
                        break
                    if not await _wait_before_retry(error, route_attempt, deadline):
                        stream_deadline_expired = True
                        break
                finally:
                    if gen is not None and not stream_started:
                        await _close_stream(gen)

            if first_chunk is None or remaining_gen is None:
                if route_error is not None:
                    logger.warning(
                        "Failover: %s/%s stream failed (%s), trying next",
                        route.provider_id,
                        route.model_id,
                        type(route_error).__name__,
                    )
                continue

            async def _stream_events(
                _first: str,
                _gen: AsyncIterator[str],
                _route: RoutingRoute,
                _attempt_started: float,
            ) -> AsyncIterator[str]:
                outcome_error: Exception | None = RuntimeError("stream closed before completion")
                try:
                    yield f"data: {_first}\n\n"
                    while True:
                        try:
                            chunk = await _next_stream_chunk(_gen)
                        except StopAsyncIteration:
                            break
                        yield f"data: {chunk}\n\n"
                    yield "data: [DONE]\n\n"
                    outcome_error = None
                except (GeneratorExit, asyncio.CancelledError):
                    outcome_error = None
                    raise
                except Exception as error:
                    outcome_error = error
                    logger.error("Stream error from %s/%s: %r", _route.provider_id, _route.model_id, error)
                    yield f"data: {json.dumps(_stream_error_payload(error))}\n\n"
                finally:
                    await _close_stream(_gen)
                    latency_ms = _record_attempt(_route, _attempt_started, outcome_error, max_tokens=max_tokens)
                    if _structured_logger:
                        _structured_logger.log_request(
                            method=request.method,
                            path=request.url.path,
                            status_code=200 if outcome_error is None else 502,
                            duration_ms=latency_ms,
                            request_id=request_id,
                            provider=_route.provider_id,
                            model=_route.model_id,
                        )

            return StreamingResponse(
                _stream_events(first_chunk, remaining_gen, route, attempt_started),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    **_metadata_headers(route.provider_id, route.model_id),
                },
            )

        status_code = _failure_status(stream_last_error, stream_only_timeouts, stream_deadline_expired)
        message = (
            "All providers timed out for streaming" if status_code == 504 else "All providers failed for streaming"
        )
        logger.warning(
            "All providers failed for streaming: %s (status=%s)",
            type(stream_last_error).__name__ if stream_last_error is not None else "no-candidate",
            status_code,
        )
        return JSONResponse({"error": message}, status_code=status_code)

    deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
    attempts = 0
    last_error: Exception | None = None
    only_timeouts = True
    deadline_expired = False

    for route in candidates:
        if _rpm_governor is not None:
            await _rpm_governor.acquire(route.provider_id)
        if attempts >= _MAX_REQUEST_ATTEMPTS:
            break
        if _remaining(deadline) <= 0:
            deadline_expired = True
            break

        adapter = _adapter_registry.get(route.provider_id)
        if adapter is None:
            continue

        chat_req.model = route.model_id
        chat_route_error: Exception | None = None

        for route_attempt in range(_MAX_ROUTE_ATTEMPTS):
            if attempts >= _MAX_REQUEST_ATTEMPTS:
                break
            if _remaining(deadline) <= 0:
                deadline_expired = True
                break
            if not _acquire_attempt(route):
                break

            attempts += 1
            attempt_started = time.monotonic()
            chat_operation: Callable[[], Awaitable[ChatResponse]] = partial(
                _chat_operation,
                adapter,
                chat_req,
                route.model_id,
            )

            try:
                route_response = await _call_with_deadline(chat_operation, deadline)
            except Exception as error:
                chat_route_error = error
                last_error = error
                only_timeouts = only_timeouts and _is_timeout_error(error)
                _record_attempt(route, attempt_started, error, max_tokens=max_tokens)
                if not (
                    _is_retryable_error(error)
                    and route_attempt + 1 < _MAX_ROUTE_ATTEMPTS
                    and attempts < _MAX_REQUEST_ATTEMPTS
                    and _remaining(deadline) > 0
                ):
                    break
                if not await _wait_before_retry(error, route_attempt, deadline):
                    deadline_expired = True
                    break
                continue

            tokens = route_response.usage.total_tokens if route_response.usage else 0
            latency_ms = _record_attempt(route, attempt_started, tokens=tokens)
            resp_json = route_response.model_dump()

            registry = _plugin_registry
            if registry is not None:
                resp_json = await registry.run_response_hooks(resp_json)

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

            return JSONResponse(
                resp_json,
                headers=_metadata_headers(route.provider_id, route.model_id),
            )

        if chat_route_error is not None:
            if _structured_logger:
                _structured_logger.log_error(
                    message=f"Failover: {route.provider_id}/{route.model_id} failed ({type(chat_route_error).__name__})",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    provider=route.provider_id,
                )
            logger.warning(
                "Failover: %s/%s failed (%s), trying next",
                route.provider_id,
                route.model_id,
                type(chat_route_error).__name__,
            )

    status_code = _failure_status(last_error, only_timeouts, deadline_expired)
    if status_code == 504:
        return JSONResponse({"error": "All providers timed out"}, status_code=status_code)
    return JSONResponse(
        {"error": f"All providers exhausted for model: {model_input}"},
        status_code=status_code,
    )


async def anthropic_messages(request: Request) -> JSONResponse | StreamingResponse:
    request_id = generate_request_id()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            create_anthropic_error(400, "Invalid JSON body"),
            status_code=400,
        )

    registry = _plugin_registry
    if registry is not None:
        body = await registry.run_request_hooks(body)

    try:
        chat_req = anthropic_to_chat_request(body)
    except Exception as exc:
        return JSONResponse(
            create_anthropic_error(400, f"Invalid request: {exc}"),
            status_code=400,
        )

    model_input = chat_req.model
    max_tokens = chat_req.max_tokens

    if _routing_engine is None or _alias_resolver is None:
        return JSONResponse(
            create_anthropic_error(500, "Routing engine not initialized"),
            status_code=500,
        )

    try:
        candidates = _get_routing_candidates(model_input, max_tokens=max_tokens, messages=[m.model_dump() for m in chat_req.messages])
    except AliasResolutionError as error:
        return JSONResponse(
            create_anthropic_error(400, str(error)),
            status_code=400,
        )
    if not candidates:
        if _routing_engine is not None:
            logger.warning(
                "No routes for model %s (anthropic): %s",
                model_input,
                _routing_engine.explain_empty_candidates(model_input, max_tokens=max_tokens),
            )
        return JSONResponse(
            create_anthropic_error(503, f"No routes for model: {model_input}"),
            status_code=503,
        )

    if _adapter_registry is None:
        return JSONResponse(
            create_anthropic_error(500, "Adapter registry not initialized"),
            status_code=500,
        )

    is_stream = body.get("stream", False)

    if is_stream:
        attempts = 0
        stream_last_error: Exception | None = None
        stream_only_timeouts = True
        stream_deadline_expired = False

        for route in candidates:
            if _rpm_governor is not None:
                await _rpm_governor.acquire(route.provider_id)
            deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
            if attempts >= _MAX_REQUEST_ATTEMPTS:
                break
            if _remaining(deadline) <= 0:
                stream_deadline_expired = True
                break

            adapter = _adapter_registry.get(route.provider_id)
            if adapter is None:
                continue

            chat_req.model = route.model_id
            first_chunk: str | None = None
            remaining_gen: AsyncIterator[str] | None = None
            attempt_started = 0.0
            route_error: Exception | None = None

            for route_attempt in range(_MAX_ROUTE_ATTEMPTS):
                if attempts >= _MAX_REQUEST_ATTEMPTS:
                    break
                if _remaining(deadline) <= 0:
                    stream_deadline_expired = True
                    break
                if not _acquire_attempt(route):
                    continue

                attempts += 1
                attempt_started = time.monotonic()
                gen: AsyncIterator[str] | None = None
                stream_started = False
                try:
                    gen = adapter.chat_completion_stream(chat_req, route.model_id)
                    first_chunk = await _call_with_deadline(gen.__anext__, deadline)
                    remaining_gen = gen
                    stream_started = True
                    break
                except Exception as error:
                    route_error = error
                    stream_last_error = error
                    stream_only_timeouts = stream_only_timeouts and _is_timeout_error(error)
                    _record_attempt(route, attempt_started, error, max_tokens=max_tokens)
                    if not (
                        _is_retryable_error(error)
                        and route_attempt + 1 < _MAX_ROUTE_ATTEMPTS
                        and attempts < _MAX_REQUEST_ATTEMPTS
                        and _remaining(deadline) > 0
                    ):
                        break
                    if not await _wait_before_retry(error, route_attempt, deadline):
                        stream_deadline_expired = True
                        break
                finally:
                    if gen is not None and not stream_started:
                        await _close_stream(gen)

            if first_chunk is None or remaining_gen is None:
                if route_error is not None:
                    logger.warning(
                        "Failover (anthropic): %s/%s stream failed (%s), trying next",
                        route.provider_id,
                        route.model_id,
                        type(route_error).__name__,
                    )
                continue

            async def _anthropic_stream_events(
                _first: str,
                _gen: AsyncIterator[str],
                _route: RoutingRoute,
                _attempt_started: float,
            ) -> AsyncIterator[str]:
                outcome_error: Exception | None = RuntimeError("stream closed before completion")
                try:
                    for sse_event in openai_chunk_to_anthropic_sse(json.loads(_first)):
                        yield sse_event
                    while True:
                        try:
                            chunk = await _next_stream_chunk(_gen)
                        except StopAsyncIteration:
                            break
                        try:
                            chunk_data = json.loads(chunk)
                            for sse_event in openai_chunk_to_anthropic_sse(chunk_data):
                                yield sse_event
                        except (json.JSONDecodeError, TypeError) as chunk_error:
                            logger.debug("Skipping malformed upstream chunk: %r", chunk_error)
                    outcome_error = None
                except (GeneratorExit, asyncio.CancelledError):
                    outcome_error = None
                    raise
                except Exception as error:
                    outcome_error = error
                    logger.error("Stream error (anthropic) from %s/%s: %r", _route.provider_id, _route.model_id, error)
                    yield _sse_event(create_anthropic_error(_stream_error_status(error), _stream_error_message(error)))
                finally:
                    await _close_stream(_gen)
                    _record_attempt(_route, _attempt_started, outcome_error, max_tokens=max_tokens)

            return StreamingResponse(
                _anthropic_stream_events(first_chunk, remaining_gen, route, attempt_started),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        status_code = _failure_status(stream_last_error, stream_only_timeouts, stream_deadline_expired)
        message = (
            "All providers timed out for streaming" if status_code == 504 else "All providers failed for streaming"
        )
        logger.warning(
            "All providers failed for streaming (anthropic): %s (status=%s)",
            type(stream_last_error).__name__ if stream_last_error is not None else "no-candidate",
            status_code,
        )
        return JSONResponse(create_anthropic_error(status_code, message), status_code=status_code)

    deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
    attempts = 0
    last_error: Exception | None = None
    only_timeouts = True
    deadline_expired = False

    for route in candidates:
        if _rpm_governor is not None:
            await _rpm_governor.acquire(route.provider_id)
        if attempts >= _MAX_REQUEST_ATTEMPTS:
            break
        if _remaining(deadline) <= 0:
            deadline_expired = True
            break

        adapter = _adapter_registry.get(route.provider_id)
        if adapter is None:
            continue

        chat_req.model = route.model_id
        chat_route_error: Exception | None = None

        for route_attempt in range(_MAX_ROUTE_ATTEMPTS):
            if attempts >= _MAX_REQUEST_ATTEMPTS:
                break
            if _remaining(deadline) <= 0:
                deadline_expired = True
                break
            if not _acquire_attempt(route):
                break

            attempts += 1
            attempt_started = time.monotonic()
            chat_operation: Callable[[], Awaitable[ChatResponse]] = partial(
                _chat_operation,
                adapter,
                chat_req,
                route.model_id,
            )

            try:
                route_response = await _call_with_deadline(chat_operation, deadline)
            except Exception as error:
                chat_route_error = error
                last_error = error
                only_timeouts = only_timeouts and _is_timeout_error(error)
                _record_attempt(route, attempt_started, error, max_tokens=max_tokens)
                if not (
                    _is_retryable_error(error)
                    and route_attempt + 1 < _MAX_ROUTE_ATTEMPTS
                    and attempts < _MAX_REQUEST_ATTEMPTS
                    and _remaining(deadline) > 0
                ):
                    break
                if not await _wait_before_retry(error, route_attempt, deadline):
                    deadline_expired = True
                    break
                continue

            tokens = route_response.usage.total_tokens if route_response.usage else 0
            latency_ms = _record_attempt(route, attempt_started, tokens=tokens)
            resp_json = chat_response_to_anthropic(route_response.model_dump())

            registry = _plugin_registry
            if registry is not None:
                resp_json = await registry.run_response_hooks(resp_json)

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

        if chat_route_error is not None:
            logger.warning(
                "Failover (anthropic): %s/%s failed (%s), trying next",
                route.provider_id,
                route.model_id,
                type(chat_route_error).__name__,
            )

    status_code = _failure_status(last_error, only_timeouts, deadline_expired)
    if status_code == 504:
        return JSONResponse(create_anthropic_error(504, "All providers timed out"), status_code=504)
    return JSONResponse(
        create_anthropic_error(status_code, f"All providers exhausted for model: {model_input}"),
        status_code=status_code,
    )


async def stats_endpoint(request: Request) -> JSONResponse:
    if _stats is None:
        return JSONResponse({"error": "Stats not initialized"}, status_code=500)
    summary = _stats.get_summary()
    if _health is not None:
        breakers = _health.get_summary()
        summary["circuit_breakers"] = {
            key: state for key, state in breakers.items() if state["state"] != "closed"
        }
        summary["circuit_breakers_open"] = sum(1 for s in breakers.values() if s["state"] == "open")
        summary["circuit_breakers_half_open"] = sum(1 for s in breakers.values() if s["state"] == "half-open")
    return JSONResponse(summary)


async def embeddings(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _invalid_request_response("Invalid JSON body", code="invalid_json")

    registry = _plugin_registry
    if registry is not None:
        body = await registry.run_request_hooks(body)

    try:
        emb_req = EmbeddingRequest.model_validate(body)
    except ValidationError as error:
        return _validation_error_response(error)

    model_input = emb_req.model

    if _routing_engine is None or _adapter_registry is None or _alias_resolver is None:
        return JSONResponse({"error": "Routing not initialized"}, status_code=500)

    try:
        candidates = _get_routing_candidates(model_input)
    except AliasResolutionError as error:
        return _invalid_request_response(str(error), param="model", code="invalid_model")
    if not candidates:
        if _routing_engine is not None:
            logger.warning(
                "No routes for model %s: %s",
                model_input,
                _routing_engine.explain_empty_candidates(model_input),
            )
        return JSONResponse({"error": f"No routes for model: {model_input}"}, status_code=503)

    deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
    attempts = 0
    last_error: Exception | None = None
    only_timeouts = True
    deadline_expired = False

    for route in candidates:
        if _rpm_governor is not None:
            await _rpm_governor.acquire(route.provider_id)
        if attempts >= _MAX_REQUEST_ATTEMPTS:
            break
        if _remaining(deadline) <= 0:
            deadline_expired = True
            break

        adapter = _adapter_registry.get(route.provider_id)
        if adapter is None:
            continue

        emb_req.model = route.model_id
        embedding_route_error: Exception | None = None
        for route_attempt in range(_MAX_ROUTE_ATTEMPTS):
            if attempts >= _MAX_REQUEST_ATTEMPTS:
                break
            if _remaining(deadline) <= 0:
                deadline_expired = True
                break
            if not _acquire_attempt(route):
                break

            attempts += 1
            attempt_started = time.monotonic()
            embedding_operation: Callable[[], Awaitable[EmbeddingResponse]] = partial(
                _embedding_operation,
                adapter,
                emb_req,
                route.model_id,
            )

            try:
                response = await _call_with_deadline(embedding_operation, deadline)
            except Exception as error:
                embedding_route_error = error
                last_error = error
                only_timeouts = only_timeouts and _is_timeout_error(error)
                _record_attempt(route, attempt_started, error)
                if not (
                    _is_retryable_error(error)
                    and route_attempt + 1 < _MAX_ROUTE_ATTEMPTS
                    and attempts < _MAX_REQUEST_ATTEMPTS
                    and _remaining(deadline) > 0
                ):
                    break
                if not await _wait_before_retry(error, route_attempt, deadline):
                    deadline_expired = True
                    break
                continue

            tokens = response.usage.total_tokens if response.usage else 0
            _record_attempt(route, attempt_started, tokens=tokens)
            resp_json = response.model_dump()

            registry = _plugin_registry
            if registry is not None:
                resp_json = await registry.run_response_hooks(resp_json)
            return JSONResponse(
                resp_json,
                headers=_metadata_headers(route.provider_id, route.model_id),
            )

        if embedding_route_error is not None:
            logger.warning(
                "Failover embeddings: %s/%s failed (%s)",
                route.provider_id,
                route.model_id,
                type(embedding_route_error).__name__,
            )

    status_code = _failure_status(last_error, only_timeouts, deadline_expired)
    message = (
        "All providers timed out for embeddings"
        if status_code == 504
        else f"All providers exhausted for embeddings model: {model_input}"
    )
    return JSONResponse(
        {"error": message},
        status_code=status_code,
    )


async def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


async def metrics_handler(request: Request) -> Response:
    return _metrics_endpoint()


async def mcp_endpoint(request: Request) -> JSONResponse:
    if _mcp_server is None:
        return JSONResponse({"error": "MCP server not initialized"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    response = _mcp_server._handle_request(body)
    return JSONResponse(response)


def create_app(settings: Settings | None = None) -> Starlette:
    global _readiness

    if _client is None:
        _readiness = ReadinessState(ready=False, reason="startup_incomplete")

    app = Starlette(
        routes=[
            Route("/", dashboard, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/v1/embeddings", embeddings, methods=["POST"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/providers", list_providers, methods=["GET"]),
            Route("/healthz", health_check, methods=["GET"]),
            Route("/readyz", ready_check, methods=["GET"]),
            Route("/stats", stats_endpoint, methods=["GET"]),
            Route("/metrics", metrics_handler, methods=["GET"]),
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/v1/messages", anthropic_messages, methods=["POST"]),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(AuthMiddleware),
            Middleware(SecurityHeadersMiddleware),
            Middleware(BodySizeLimitMiddleware, max_body_size=1_048_576),
        ],
    )
    return app
