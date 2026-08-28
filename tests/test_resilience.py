from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import sparrow.app as app_module
from sparrow.app import lifespan
from sparrow.middleware.auth import get_api_key_auth
from sparrow.models import ChatChoice, ChatMessage, ChatResponse, EmbeddingData, EmbeddingResponse
from sparrow.routing.engine import Route, RoutingEngine
from sparrow.routing.health import CircuitBreaker, RouteHealthTracker
from sparrow.routing.quota import QuotaTracker


def _status_error(status_code: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://upstream.test/v1")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    response = httpx.Response(status_code, request=request, headers=headers)
    return httpx.HTTPStatusError("upstream failure", request=request, response=response)


def _chat_response(model: str) -> ChatResponse:
    return ChatResponse(
        id="chatcmpl-resilience",
        created=1,
        model=model,
        choices=[ChatChoice(message=ChatMessage(role="assistant", content="ok"))],
    )


def _embedding_response(model: str) -> EmbeddingResponse:
    return EmbeddingResponse(model=model, data=[EmbeddingData(embedding=[0.1], index=0)])


class _FailingChatAdapter:
    def __init__(self, error_factory) -> None:
        self.calls = 0
        self._error_factory = error_factory

    async def chat_completion(self, request, model):
        self.calls += 1
        raise self._error_factory()


class _RetryingEmbeddingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def embedding(self, request, model):
        self.calls += 1
        if self.calls == 1:
            raise _status_error(503)
        return _embedding_response(model)


class _RetryingStreamAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def chat_completion_stream(self, request, model):
        self.calls += 1
        if self.calls == 1:
            return _ErrorStream(_status_error(503))
        return _SuccessStream("first")


class _ErrorStream:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._error

    async def aclose(self) -> None:
        self.closed = True


class _SuccessStream:
    def __init__(self, chunk: str) -> None:
        self._chunk = chunk
        self._sent = False
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return self._chunk

    async def aclose(self) -> None:
        self.closed = True


class _Registry:
    def __init__(self, adapters) -> None:
        self._adapters = adapters

    def get(self, provider_id):
        return self._adapters.get(provider_id)


def _request_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-key"}


def _chat_payload() -> dict[str, object]:
    return {"model": "auto", "messages": [{"role": "user", "content": "hello"}]}


def test_quota_try_acquire_is_atomic_and_counts_dispatched_attempts():
    quota = QuotaTracker()

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(
            executor.map(
                lambda _: quota.try_acquire("provider", "model", limit=4),
                range(16),
            )
        )

    assert sum(results) == 4
    assert quota.get_usage("provider", "model") == 4


def test_quota_none_is_unlimited_and_negative_limits_are_rejected():
    quota = QuotaTracker()

    assert all(quota.try_acquire("provider", "model", limit=None) for _ in range(5))
    assert quota.get_usage("provider", "model") == 5
    with pytest.raises(ValueError, match="non-negative"):
        quota.try_acquire("provider", "model", limit=-2)


def test_route_daily_quota_is_applied_before_dispatch():
    quota = QuotaTracker()
    engine = RoutingEngine(quota=quota)
    engine.register_route(Route("provider", "model", daily_quota=1))

    assert len(engine.get_candidates("model")) == 1
    assert quota.try_acquire("provider", "model", limit=1)
    assert engine.get_candidates("model") == []


def test_circuit_breaker_allows_one_half_open_probe():
    breaker = CircuitBreaker(failure_threshold=1, recovery_time=10)

    with patch("sparrow.routing.health.time.time", side_effect=[100.0, 111.0, 111.0]):
        breaker.record_failure()
        assert breaker.should_allow() is True
        assert breaker.should_allow() is False

    assert breaker.state == "half-open"
    breaker.record_success()
    assert breaker.state == "closed"


def test_circuit_breaker_cancelled_half_open_probe_can_be_reacquired():
    breaker = CircuitBreaker(failure_threshold=1, recovery_time=10)
    breaker._failures = 1
    breaker._state = "open"
    breaker._last_failure = 0.0

    with patch("sparrow.routing.health.time.time", return_value=111.0):
        assert breaker.try_acquire() is True
        breaker.cancel_acquire()

    with patch("sparrow.routing.health.time.time", return_value=122.0):
        assert breaker.try_acquire() is True


def test_route_health_tracker_creates_one_breaker_under_concurrency():
    tracker = RouteHealthTracker()

    with ThreadPoolExecutor(max_workers=16) as executor:
        breakers = list(executor.map(lambda _: tracker.get_breaker("provider:model"), range(16)))

    assert len({id(breaker) for breaker in breakers}) == 1
    assert len(tracker._breakers) == 1


@pytest.mark.asyncio
async def test_chat_retry_policy_limits_route_and_global_attempts(app, monkeypatch):
    routes = [Route("first", "model-1"), Route("second", "model-2")]
    adapters = {
        "first": _FailingChatAdapter(lambda: _status_error(500)),
        "second": _FailingChatAdapter(lambda: _status_error(500)),
    }

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry(adapters))
        sleep = AsyncMock()
        monkeypatch.setattr(app_module.asyncio, "sleep", sleep)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=_chat_payload(), headers=_request_headers())

    assert response.status_code == 503
    assert adapters["first"].calls == 2
    assert adapters["second"].calls == 2
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_non_retryable_upstream_client_error_advances_without_same_route_retry(app, monkeypatch):
    first = _FailingChatAdapter(lambda: _status_error(400))

    class _SuccessfulChatAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            return _chat_response(model)

    second = _SuccessfulChatAdapter()
    routes = [Route("first", "model-1"), Route("second", "model-2")]

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry({"first": first, "second": second}))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=_chat_payload(), headers=_request_headers())

    assert response.status_code == 200
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_timeout_retry_is_capped_and_returns_504(app, monkeypatch):
    request = httpx.Request("POST", "https://upstream.test/v1")
    adapter = _FailingChatAdapter(lambda: httpx.ReadTimeout("timed out", request=request))
    routes = [Route("timeout", "model")]

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry({"timeout": adapter}))
        sleep = AsyncMock()
        monkeypatch.setattr(app_module.asyncio, "sleep", sleep)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=_chat_payload(), headers=_request_headers())

    assert response.status_code == 504
    assert adapter.calls == 2
    assert sleep.await_count == 1
    assert sleep.await_args.args[0] <= 10


@pytest.mark.asyncio
async def test_stream_retries_retryable_error_before_first_event(app, monkeypatch):
    adapter = _RetryingStreamAdapter()
    route = Route("stream", "model")

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry({"stream": adapter}))
        monkeypatch.setattr(app_module.asyncio, "sleep", AsyncMock())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={**_chat_payload(), "stream": True},
                headers=_request_headers(),
            )

    assert response.status_code == 200
    assert "data: first" in response.text
    assert "data: [DONE]" in response.text
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_embeddings_share_retry_quota_stats_and_breaker_policy(app, monkeypatch):
    adapter = _RetryingEmbeddingAdapter()
    route = Route("embedding", "model")

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry({"embedding": adapter}))
        monkeypatch.setattr(app_module.asyncio, "sleep", AsyncMock())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/embeddings",
                json={"model": "auto", "input": "hello"},
                headers=_request_headers(),
            )

        assert response.status_code == 200
        assert adapter.calls == 2
        assert app_module._quota.get_usage("embedding", "model") == 2
        provider_stats = app_module._stats.get_provider_stats("embedding")
        assert provider_stats is not None
        assert provider_stats.requests == 2
        assert provider_stats.successes == 1
        assert app_module._health.get_breaker("embedding:model").state == "closed"

    get_api_key_auth().set_keys("test-key")


@pytest.mark.asyncio
async def test_normal_chat_attempt_persists_breaker_state_when_tracker_has_path(app, monkeypatch, tmp_path):
    class SuccessfulAdapter:
        async def chat_completion(self, request, model):
            return _chat_response(model)

    route = Route("persisted", "model")
    persist_path = tmp_path / "circuit_breakers.json"
    tracker = RouteHealthTracker(persist_path=persist_path)

    async with lifespan(app):
        engine = app_module._routing_engine
        assert engine is not None
        monkeypatch.setattr(engine, "_health", tracker)
        monkeypatch.setattr(engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", _Registry({"persisted": SuccessfulAdapter()}))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=_chat_payload(), headers=_request_headers())

        assert response.status_code == 200
        assert persist_path.exists()
        assert RouteHealthTracker(persist_path=persist_path).get_summary()["persisted:model"]["state"] == "closed"


@pytest.mark.asyncio
async def test_chat_dispatch_skips_route_that_becomes_unhealthy_after_candidate_selection(app, monkeypatch):
    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            return _chat_response(model)

    route = Route("became-unhealthy", "model")
    adapter = Adapter()

    class Registry:
        def get(self, provider_id):
            engine = app_module._routing_engine
            assert engine is not None
            breaker = engine._health.get_breaker(f"{provider_id}:model")
            for _ in range(breaker.failure_threshold):
                breaker.record_failure()
            return adapter

    async with lifespan(app):
        engine = app_module._routing_engine
        assert engine is not None
        monkeypatch.setattr(engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", Registry())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/chat/completions", json=_chat_payload(), headers=_request_headers())

    assert response.status_code == 503
    assert adapter.calls == 0
