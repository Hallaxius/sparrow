import asyncio
import json

import httpx
import pytest

import sparrow.app as app_module
from sparrow.app import lifespan
from sparrow.config.models import Settings
from sparrow.errors import ConfigError, ConfigurationFileError, UpstreamResponseError
from sparrow.middleware import auth as auth_module
from sparrow.middleware.auth import get_api_key_auth
from sparrow.models import ChatChoice, ChatMessage, ChatResponse, EmbeddingData, EmbeddingResponse
from sparrow.plugins.registry import PluginRegistry
from sparrow.routing.engine import Route as RoutingRoute


@pytest.mark.asyncio
async def test_health_check(initialized_client):
    response = await initialized_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "warp_enabled" in data


@pytest.mark.asyncio
async def test_health_check_is_live_before_lifespan(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_is_unavailable_before_lifespan(client):
    response = await client.get("/readyz")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["reason"] == "startup_incomplete"


@pytest.mark.asyncio
async def test_readiness_is_available_after_lifespan(initialized_client):
    response = await initialized_client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["ready"] is True
    assert data["routes"] > 0


@pytest.mark.asyncio
async def test_readiness_requires_an_enabled_route(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_all_providers", lambda: {"providers": {}, "aliases": {}})

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["reason"] == "no_routes"


@pytest.mark.asyncio
async def test_readiness_passes_when_warp_unavailable(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings())

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_requires_warp_when_configured(app, monkeypatch):
    async def unreachable(proxy_url: str, timeout: float = 5.0) -> bool:
        return False

    monkeypatch.setattr("sparrow.proxy.check_warp_reachable", unreachable)
    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: Settings(warp_required=True, warp_startup_timeout=0.01, warp_startup_retry_interval=0.001),
    )

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["reason"] == "warp_unavailable"
    assert response.json()["warp_required"] is True


@pytest.mark.asyncio
async def test_readiness_reflects_warp_loss_after_startup(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(warp_required=True))

    async def startup_health(self, timeout: float, retry_interval: float) -> bool:
        return True

    monkeypatch.setattr(app_module.WARPProxy, "wait_until_available", startup_health)

    async with lifespan(app):
        assert app_module._client is not None
        monkeypatch.setattr(app_module._client.warp, "is_warp_available", lambda: False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["reason"] == "warp_unavailable"


@pytest.mark.asyncio
async def test_lifespan_failure_closes_resources_and_clears_state(app, monkeypatch):
    captured: list[object] = []

    async def fail_start(self: object, monitor_warp: bool = False) -> None:
        captured.append(app_module._client)
        raise ConfigurationFileError("providers.json", "startup failure")

    monkeypatch.setattr(app_module.SparrowClient, "start", fail_start)

    with pytest.raises(ConfigurationFileError):
        async with lifespan(app):
            raise AssertionError("lifespan should fail before yielding")

    assert len(captured) == 1
    client_instance = captured[0]
    assert client_instance is not None
    assert app_module._client is None
    assert app_module._routing_engine is None
    assert app_module._adapter_registry is None
    assert app_module._stats is None
    assert app_module._quota is None
    assert app_module._health is None
    assert app_module._structured_logger is None
    assert app_module._readiness.ready is False
    assert app_module._readiness.reason == "startup_failed"


@pytest.mark.asyncio
async def test_lifespan_reentry_clears_resources_after_each_shutdown(app):
    warp_clients: list[httpx.AsyncClient] = []

    async with lifespan(app):
        assert app_module._readiness.ready is True
        assert app_module._client is not None
        assert app_module._client._warp_client is not None
        warp_clients.append(app_module._client._warp_client)

    assert app_module._client is None
    assert warp_clients[0].is_closed
    assert app_module._readiness.reason == "shutdown"

    async with lifespan(app):
        assert app_module._readiness.ready is True
        assert app_module._client is not None
        assert app_module._client._warp_client is not None
        warp_clients.append(app_module._client._warp_client)

    assert app_module._client is None
    assert warp_clients[1].is_closed
    assert app_module._readiness.reason == "shutdown"


@pytest.mark.asyncio
async def test_list_models(initialized_client):
    response = await initialized_client.get("/v1/models", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_list_providers(initialized_client):
    response = await initialized_client.get("/v1/providers", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/providers", "/stats", "/metrics"])
async def test_operational_endpoints_require_auth(client, path):
    get_api_key_auth().set_keys("test-key")
    response = await client.get(path)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_shell_requests_auth_header_for_data(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Authorization" in response.text
    assert "/v1/models" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "api_key": "test-key"},
        ),
        ("/v1/embeddings", {"model": "auto", "input": "hello", "api_key": "test-key"}),
    ],
)
async def test_body_api_key_is_not_proxy_credential(client, path, payload):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(path, json=payload)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_and_invalid_auth_use_same_response(client):
    get_api_key_auth().set_keys("test-key")
    missing = await client.get("/v1/models")
    invalid = await client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json() == invalid.json()


@pytest.mark.asyncio
async def test_bearer_auth_uses_constant_time_comparison(client, monkeypatch):
    calls: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(auth_module.secrets, "compare_digest", compare_digest)
    get_api_key_auth().set_keys("test-key")
    response = await client.get("/v1/models", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert calls == [("test-key", "test-key")]


@pytest.mark.asyncio
async def test_lifespan_requires_api_key_without_development_flag(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(api_key=""))
    with pytest.raises(ConfigError, match="SPARROW_API_KEY"):
        async with lifespan(app):
            raise AssertionError("lifespan should require an API key")


@pytest.mark.asyncio
async def test_stats(initialized_client):
    response = await initialized_client.get("/stats", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    data = response.json()
    assert "total_requests" in data
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_chat_completions_no_auth(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["error"] == "Authentication required"


@pytest.mark.asyncio
async def test_chat_completions_invalid_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-invalid-key-123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_body_key_required(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_body_key_rejected(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "api_key": "test-key",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_header_key_accepted(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_chat_completions_x_api_key_header_accepted(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_chat_completions_invalid_body_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "api_key": "wrong-key",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_embeddings_no_auth(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/embeddings",
        json={"model": "auto", "input": "hello"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_embeddings_with_valid_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/embeddings",
        json={"model": "auto", "input": "hello", "api_key": "test-key"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_returns_prometheus(initialized_client):
    response = await initialized_client.get("/metrics", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contains_expected_metrics(initialized_client):
    response = await initialized_client.get("/metrics", headers={"Authorization": "Bearer test-key"})
    body = response.text
    assert "sparrow_requests_total" in body
    assert "sparrow_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_list_providers_enrichment(initialized_client):
    response = await initialized_client.get("/v1/providers", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    for provider in data["data"]:
        assert "circuit_breaker_state" in provider
        assert "quota_used_today" in provider
        assert "avg_latency_ms" in provider
        assert "success_rate" in provider


@pytest.mark.asyncio
async def test_security_headers_present(initialized_client):
    response = await initialized_client.get("/healthz")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_chat_invalid_request_returns_safe_openai_400(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto"},
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["type"] == "invalid_request_error"
    assert "messages" in data["error"]["param"]
    assert "input" not in response.text


@pytest.mark.asyncio
async def test_embedding_invalid_request_returns_safe_openai_400(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/embeddings",
        json={"model": "auto"},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["type"] == "invalid_request_error"
    assert "input" in data["error"]["param"]
    assert '"input":' not in response.text


def _chat_response(model: str) -> ChatResponse:
    return ChatResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[ChatChoice(message=ChatMessage(role="assistant", content="hello"))],
    )


@pytest.mark.asyncio
async def test_chat_response_metadata_is_in_headers_not_json(app, monkeypatch):
    class Adapter:
        async def chat_completion(self, request, model):
            return _chat_response(model)

    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: Adapter()})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert response.headers["x-sparrow-provider"] == "test-provider"
    assert response.headers["x-sparrow-model"] == "test-model"
    assert "x_sparrow_provider" not in response.json()
    assert "x_sparrow_model" not in response.json()


@pytest.mark.asyncio
async def test_embedding_response_metadata_is_in_headers_not_json(app, monkeypatch):
    class Adapter:
        async def embedding(self, request, model):
            return EmbeddingResponse(model=model, data=[EmbeddingData(embedding=[0.1], index=0)])

    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: Adapter()})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/embeddings",
                json={"model": "auto", "input": "hello"},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert response.headers["x-sparrow-provider"] == "test-provider"
    assert response.headers["x-sparrow-model"] == "test-model"
    assert "x_sparrow_provider" not in response.json()
    assert "x_sparrow_model" not in response.json()


@pytest.mark.asyncio
async def test_invalid_chat_upstream_response_fails_over(app, monkeypatch):
    class Adapter:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            if self.calls == 1:
                raise UpstreamResponseError("test-provider", "chat")
            return _chat_response(model)

    adapter = Adapter()
    routes = [
        RoutingRoute(provider_id="test-provider", model_id="test-model-1"),
        RoutingRoute(provider_id="test-provider", model_id="test-model-2"),
    ]

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: adapter})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert adapter.calls == 2
    assert response.headers["x-sparrow-model"] == "test-model-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["unknown-model", "unknown-alias"])
async def test_unknown_model_or_alias_returns_safe_400(initialized_client, model):
    response = await initialized_client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["param"] == "model"


@pytest.mark.asyncio
async def test_anthropic_no_routes_returns_503(app, monkeypatch):
    async with lifespan(app):
        monkeypatch.setattr(
            app_module._routing_engine,
            "get_candidates",
            lambda model, max_tokens=None, provider_id=None: [],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/messages",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "api_error"


@pytest.mark.asyncio
async def test_alias_resolves_exact_target_and_configured_quality_order(app, monkeypatch):
    providers = {
        "slow": {
            "name": "Slow",
            "base_url": "https://slow.example/v1",
            "adapter": "openai",
            "auth": "none",
            "daily_quota": None,
            "models": [
                {"id": "shared", "slug": "shared", "name": "Shared", "quality": 2, "context": 8192, "enabled": True}
            ],
        },
        "best": {
            "name": "Best",
            "base_url": "https://best.example/v1",
            "adapter": "openai",
            "auth": "none",
            "daily_quota": None,
            "models": [
                {"id": "shared", "slug": "shared", "name": "Shared", "quality": 9, "context": 8192, "enabled": True}
            ],
        },
    }
    runtime = {"providers": providers, "aliases": {"best-shared": "best/shared"}}
    monkeypatch.setattr(app_module, "load_all_providers", lambda: runtime)
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(routing="quality", api_key="test-key"))

    class Adapter:
        async def chat_completion(self, request, model):
            return _chat_response(model)

    adapters = {"slow": Adapter(), "best": Adapter()}

    async with lifespan(app):
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            alias_response = await client.post(
                "/v1/chat/completions",
                json={"model": "best-shared", "messages": [{"role": "user", "content": "hello"}]},
                headers={"Authorization": "Bearer test-key"},
            )
            direct_response = await client.post(
                "/v1/chat/completions",
                json={"model": "shared", "messages": [{"role": "user", "content": "hello"}]},
                headers={"Authorization": "Bearer test-key"},
            )

    assert alias_response.status_code == 200
    assert alias_response.headers["x-sparrow-provider"] == "best"
    assert alias_response.headers["x-sparrow-model"] == "shared"
    assert direct_response.status_code == 200
    assert direct_response.headers["x-sparrow-provider"] == "best"


class _TrackedAsyncIterator:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = iter(events)
        self.error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self.events)
        except StopIteration:
            if self.error is not None:
                error = self.error
                self.error = None
                raise error from None
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_failover_closes_failed_and_successful_iterators(app, monkeypatch):
    first = _TrackedAsyncIterator([], RuntimeError("before first event"))
    second = _TrackedAsyncIterator(["second"])
    attempt_records: list[tuple[str, str, str, str]] = []

    class Adapter:
        def __init__(self, stream):
            self.stream = stream

        def chat_completion_stream(self, request, model):
            return self.stream

    adapters = {"provider-1": Adapter(first), "provider-2": Adapter(second)}
    routes = [
        RoutingRoute(provider_id="provider-1", model_id="model-1"),
        RoutingRoute(provider_id="provider-2", model_id="model-2"),
    ]

    async with lifespan(app):
        monkeypatch.setattr(
            app_module,
            "record_attempt",
            lambda provider, model, phase, outcome: attempt_records.append((provider, model, phase, outcome)),
        )
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert "data: second\n\ndata: [DONE]\n\n" in response.text
    assert first.closed is True
    assert second.closed is True
    assert attempt_records == [
        ("provider-1", "model-1", "first_chunk", "error"),
        ("provider-2", "model-2", "stream_after_first_chunk", "success"),
    ]


@pytest.mark.asyncio
async def test_stream_error_after_first_event_emits_one_error_without_failover(app, monkeypatch, caplog):
    first = _TrackedAsyncIterator(["first"], httpx.RemoteProtocolError("after first event"))
    second_calls = 0
    attempt_records: list[tuple[str, str, str, str]] = []

    class FirstAdapter:
        def chat_completion_stream(self, request, model):
            return first

    class SecondAdapter:
        def chat_completion_stream(self, request, model):
            nonlocal second_calls
            second_calls += 1
            return _TrackedAsyncIterator(["unexpected"])

    routes = [
        RoutingRoute(provider_id="provider-1", model_id="model-1"),
        RoutingRoute(provider_id="provider-2", model_id="model-2"),
    ]
    adapters = {"provider-1": FirstAdapter(), "provider-2": SecondAdapter()}

    async with lifespan(app):
        monkeypatch.setattr(
            app_module,
            "record_attempt",
            lambda provider, model, phase, outcome: attempt_records.append((provider, model, phase, outcome)),
        )
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert response.text.count('"type": "upstream_error"') == 1
    assert "data: [DONE]" not in response.text
    assert second_calls == 0
    assert first.closed is True
    assert attempt_records == [("provider-1", "model-1", "stream_after_first_chunk", "transport_error")]
    error_entries = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "sparrow" and "Upstream request failed" in record.getMessage()
    ]
    assert len(error_entries) == 1
    assert error_entries[0]["error_type"] == "RemoteProtocolError"
    assert error_entries[0]["phase"] == "stream_after_first_chunk"


@pytest.mark.asyncio
async def test_stream_failover_skips_quota_exhausted_candidate(app, monkeypatch):
    first_calls = 0
    second = _TrackedAsyncIterator(["second"])

    class QuotaAdapter:
        def chat_completion_stream(self, request, model):
            return _TrackedAsyncIterator(["unexpected"])

    class SecondAdapter:
        def chat_completion_stream(self, request, model):
            return second

    adapters = {"provider-1": QuotaAdapter(), "provider-2": SecondAdapter()}
    routes = [
        RoutingRoute(provider_id="provider-1", model_id="model-1"),
        RoutingRoute(provider_id="provider-2", model_id="model-2"),
    ]

    def _fake_acquire(route):
        nonlocal first_calls
        if route.provider_id == "provider-1":
            first_calls += 1
            return False
        return True

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(app_module, "_acquire_attempt", _fake_acquire)
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert "data: second\n\ndata: [DONE]\n\n" in response.text
    assert first_calls >= 1
    assert second.closed is True


@pytest.mark.asyncio
async def test_stream_failover_uses_one_absolute_deadline_across_candidates(app, monkeypatch):
    class Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = Clock()

    class ExpiredStream:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            clock.now = 1.0
            raise httpx.ReadTimeout("deadline elapsed")

        async def aclose(self) -> None:
            self.closed = True

    expired = ExpiredStream()

    class ExpiredAdapter:
        def chat_completion_stream(self, request, model):
            return expired

    class LaterAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def chat_completion_stream(self, request, model):
            self.calls += 1
            return _TrackedAsyncIterator(["unexpected"])

    later = LaterAdapter()
    adapters = {"provider-1": ExpiredAdapter(), "provider-2": later}
    routes = [
        RoutingRoute(provider_id="provider-1", model_id="model-1"),
        RoutingRoute(provider_id="provider-2", model_id="model-2"),
    ]

    async with lifespan(app):
        monkeypatch.setattr(app_module, "_REQUEST_DEADLINE_SECONDS", 0.05)
        monkeypatch.setattr(app_module.time, "monotonic", clock.monotonic)
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 504
    assert later.calls == 0
    assert expired.closed is True


def _single_route_stream_setup(monkeypatch, app, adapters):
    routes = [RoutingRoute(provider_id="provider-1", model_id="model-1")]
    monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
    monkeypatch.setattr(
        app_module,
        "_adapter_registry",
        type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
    )


@pytest.mark.asyncio
async def test_stream_enforces_request_deadline_after_first_chunk(app, monkeypatch):
    class Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = Clock()
    stream = _TrackedAsyncIterator(["data-1", "data-2"])

    async def next_chunk_after_deadline(upstream, deadline=None):
        clock.now = 1.0
        if deadline is not None and deadline <= clock.now:
            raise TimeoutError("request deadline exceeded")
        return await upstream.__anext__()

    class Adapter:
        def chat_completion_stream(self, request, model):
            return stream

    adapters = {"provider-1": Adapter()}

    async with lifespan(app):
        monkeypatch.setattr(app_module, "_REQUEST_DEADLINE_SECONDS", 0.05)
        monkeypatch.setattr(app_module.time, "monotonic", clock.monotonic)
        monkeypatch.setattr(app_module, "_next_stream_chunk", next_chunk_after_deadline)
        _single_route_stream_setup(monkeypatch, app, adapters)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert "data: data-1" in response.text
    assert response.text.count("Upstream stream timed out") == 1
    assert '"type": "upstream_error"' in response.text
    assert "data: data-2" not in response.text
    assert "data: [DONE]" not in response.text
    assert stream.closed is True


@pytest.mark.asyncio
async def test_stream_idle_timeout_emits_timeout_message(app, monkeypatch):
    monkeypatch.setattr(app_module, "_STREAM_IDLE_TIMEOUT_SECONDS", 0.05)

    async def stalled_after_first():
        yield "first"
        await asyncio.sleep(1.0)
        yield "late"

    class Adapter:
        def chat_completion_stream(self, request, model):
            return stalled_after_first()

    adapters = {"provider-1": Adapter()}

    async with lifespan(app):
        _single_route_stream_setup(monkeypatch, app, adapters)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert response.text.count("Upstream stream timed out") == 1
    assert '"type": "upstream_error"' in response.text
    assert "data: late" not in response.text
    assert "data: [DONE]" not in response.text


@pytest.mark.asyncio
async def test_stream_client_abort_closes_upstream_without_recording_attempt(app, monkeypatch, caplog):
    class WaitingStream:
        def __init__(self) -> None:
            self.sent_first = False
            self.closed = False
            self.waiting = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if not self.sent_first:
                self.sent_first = True
                return "chunk-1"
            await self.waiting.wait()
            raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    stream = WaitingStream()
    caplog.set_level("INFO", logger="sparrow")
    attempt_records: list[tuple[str, str, str, str]] = []
    cancelled_attempts: list[RoutingRoute] = []

    class Adapter:
        def chat_completion_stream(self, request, model):
            return stream

    adapters = {"provider-1": Adapter()}

    async with lifespan(app):
        monkeypatch.setattr(
            app_module,
            "record_attempt",
            lambda provider, model, phase, outcome: attempt_records.append((provider, model, phase, outcome)),
        )
        monkeypatch.setattr(app_module, "_cancel_attempt", lambda route: cancelled_attempts.append(route))
        _single_route_stream_setup(monkeypatch, app, adapters)
        body = b'{"model":"auto","messages":[{"role":"user","content":"hello"}],"stream":true}'

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = app_module.Request(
            {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []}, receive
        )
        response = await app_module.chat_completions(request)
        body_iterator = response.body_iterator
        assert await body_iterator.__anext__() == "data: chunk-1\n\n"
        await body_iterator.aclose()

        engine = app_module._routing_engine
        assert engine is not None
        health = engine._health
        assert health is not None
        breaker = health.get_breaker("provider-1:model-1")
        assert breaker.failures == 0
        assert app_module._stats is not None
        assert app_module._stats.get_provider_stats("provider-1") is None
        assert stream.closed is True
        assert len(cancelled_attempts) == 1
        assert cancelled_attempts[0].provider_id == "provider-1"
        assert cancelled_attempts[0].model_id == "model-1"
        assert attempt_records == [("provider-1", "model-1", "stream_after_first_chunk", "client_cancelled")]
        cancellation_entries = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.name == "sparrow" and '"event": "client_cancelled"' in record.getMessage()
        ]
        assert len(cancellation_entries) == 1
        assert cancellation_entries[0]["phase"] == "stream_after_first_chunk"
        assert "status" not in cancellation_entries[0]


class LifecyclePlugin:
    def __init__(self):
        self.started = False
        self.stopped = False

    @property
    def name(self):
        return "lifecycle"

    @property
    def version(self):
        return "1.0"

    async def on_startup(self):
        self.started = True

    async def on_shutdown(self):
        self.stopped = True

    async def on_request(self, request):
        return request

    async def on_response(self, response):
        return response


def _seeded_registry_factory(monkeypatch, plugin):
    class SeededRegistry(PluginRegistry):
        def __init__(self):
            super().__init__()
            self.register(plugin)

    monkeypatch.setattr(app_module, "PluginRegistry", SeededRegistry)


@pytest.mark.asyncio
async def test_plugin_lifecycle_startup_and_shutdown(app, monkeypatch):
    plugin = LifecyclePlugin()
    _seeded_registry_factory(monkeypatch, plugin)

    async with lifespan(app):
        assert plugin.started is True
        assert plugin.stopped is False

    assert plugin.stopped is True


class MutatingHookPlugin:
    def __init__(self):
        self.request_calls = 0
        self.response_calls = 0

    @property
    def name(self):
        return "mutating-hooks"

    @property
    def version(self):
        return "1.0"

    async def on_startup(self):
        return None

    async def on_shutdown(self):
        return None

    async def on_request(self, request):
        self.request_calls += 1
        request["temperature"] = 0.9
        return request

    async def on_response(self, response):
        self.response_calls += 1
        response["plugin_marker"] = "applied"
        return response


class TemperatureCapturingAdapter:
    def __init__(self):
        self.seen_temperature = None

    async def chat_completion(self, request, model):
        self.seen_temperature = request.temperature
        return _chat_response(model)


@pytest.mark.asyncio
async def test_plugin_hooks_mutate_non_streaming_request_and_response(app, monkeypatch):
    plugin = MutatingHookPlugin()
    adapter = TemperatureCapturingAdapter()
    routes = [RoutingRoute(provider_id="provider-1", model_id="model-1")]
    _seeded_registry_factory(monkeypatch, plugin)

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
        monkeypatch.setattr(
            app_module,
            "_adapter_registry",
            type("Registry", (), {"get": lambda self, provider_id: adapter})(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert response.json()["plugin_marker"] == "applied"
    assert adapter.seen_temperature == 0.9


@pytest.mark.asyncio
async def test_streaming_bypasses_response_hooks(app, monkeypatch):
    plugin = MutatingHookPlugin()
    _seeded_registry_factory(monkeypatch, plugin)

    async def single_chunk():
        yield "chunk"

    class Adapter:
        def chat_completion_stream(self, request, model):
            return single_chunk()

    adapters = {"provider-1": Adapter()}

    async with lifespan(app):
        _single_route_stream_setup(monkeypatch, app, adapters)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert plugin.request_calls == 1
    assert plugin.response_calls == 0


@pytest.mark.asyncio
async def test_extra_body_reaches_adapter_payload(app, monkeypatch):
    captured = {}

    class CapturingAdapter:
        async def chat_completion(self, request, model):
            captured["extra_body"] = request.extra_body
            return _chat_response(model)

    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(
            app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: CapturingAdapter()})()
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hello"}],
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
                },
                headers={"Authorization": "Bearer test-key"},
            )

    assert response.status_code == 200
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
