import asyncio

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
async def test_readiness_reports_unavailable_required_warp(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings())
    monkeypatch.setattr("sparrow.proxy.check_warp_reachable", lambda proxy_url: _unreachable_warp())

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["reason"] == "warp_unavailable"


async def _unreachable_warp() -> bool:
    return False


@pytest.mark.asyncio
async def test_lifespan_failure_closes_resources_and_clears_state(app, monkeypatch):
    captured: list[object] = []

    async def fail_start(self: object) -> None:
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
    assert app_module._cache is None
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
async def test_deterministic_chat_uses_opt_in_cache(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(cache_enabled=True, api_key="test-key"))
    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    class Adapter:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            return _chat_response(model)

    adapter = Adapter()

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: adapter})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}]}
            first = await client.post(
                "/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"}
            )
            second = await client.post(
                "/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"}
            )

        assert app_module._cache is not None

    assert first.status_code == 200
    assert second.status_code == 200
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_cache_is_disabled_by_default(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(api_key="test-key"))
    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    class Adapter:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            return _chat_response(model)

    adapter = Adapter()

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: adapter})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}]}
            await client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"})
            await client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"})

        assert app_module._cache is None

    assert adapter.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"temperature": 0.7},
        {"tools": [{"type": "function", "function": {"name": "lookup"}}]},
        {"response_format": {"type": "json_object"}},
    ],
)
async def test_non_deterministic_chat_parameters_bypass_cache(app, monkeypatch, extra):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(cache_enabled=True, api_key="test-key"))
    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    class Adapter:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, request, model):
            self.calls += 1
            return _chat_response(model)

    adapter = Adapter()

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: adapter})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}], **extra}
            await client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"})
            await client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key"})

    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_streaming_and_embeddings_bypass_cache(app, monkeypatch):
    monkeypatch.setattr(app_module, "load_config", lambda: Settings(cache_enabled=True, api_key="test-key"))
    route = RoutingRoute(provider_id="test-provider", model_id="test-model")

    class Adapter:
        def __init__(self):
            self.stream_calls = 0
            self.embedding_calls = 0

        def chat_completion_stream(self, request, model):
            self.stream_calls += 1
            return _TrackedAsyncIterator(["chunk"])

        async def embedding(self, request, model):
            self.embedding_calls += 1
            return EmbeddingResponse(model=model, data=[EmbeddingData(embedding=[0.1], index=0)])

    adapter = Adapter()

    async with lifespan(app):
        monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: [route])
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: adapter})())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            stream_payload = {
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }
            await client.post("/v1/chat/completions", json=stream_payload, headers={"Authorization": "Bearer test-key"})
            await client.post("/v1/chat/completions", json=stream_payload, headers={"Authorization": "Bearer test-key"})
            embedding_payload = {"model": "auto", "input": "hello"}
            await client.post("/v1/embeddings", json=embedding_payload, headers={"Authorization": "Bearer test-key"})
            await client.post("/v1/embeddings", json=embedding_payload, headers={"Authorization": "Bearer test-key"})

    assert adapter.stream_calls == 2
    assert adapter.embedding_calls == 2


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


@pytest.mark.asyncio
async def test_stream_error_after_first_event_emits_one_error_without_failover(app, monkeypatch):
    first = _TrackedAsyncIterator(["first"], RuntimeError("after first event"))
    second_calls = 0

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
async def test_stream_failover_resets_deadline_per_candidate(app, monkeypatch):
    class _SlowAsyncIterator:
        def __init__(self, events, error=None):
            self.events = iter(events)
            self.error = error
            self.closed = False
            self._first = True

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if self._first:
                self._first = False
                await asyncio.sleep(0.2)
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

    fast = _TrackedAsyncIterator(["fast"])

    class SlowAdapter:
        def __init__(self):
            self.last_stream = None

        def chat_completion_stream(self, request, model):
            self.last_stream = _SlowAsyncIterator(["slow"])
            return self.last_stream

    slow_adapter = SlowAdapter()

    class FastAdapter:
        def chat_completion_stream(self, request, model):
            return fast

    adapters = {"provider-1": slow_adapter, "provider-2": FastAdapter()}
    routes = [
        RoutingRoute(provider_id="provider-1", model_id="model-1"),
        RoutingRoute(provider_id="provider-2", model_id="model-2"),
    ]

    async with lifespan(app):
        monkeypatch.setattr(app_module, "_REQUEST_DEADLINE_SECONDS", 0.05)
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
    assert "data: fast\n\ndata: [DONE]\n\n" in response.text
    assert slow_adapter.last_stream is not None and slow_adapter.last_stream.closed is True
    assert fast.closed is True


def _single_route_stream_setup(monkeypatch, app, adapters):
    routes = [RoutingRoute(provider_id="provider-1", model_id="model-1")]
    monkeypatch.setattr(app_module._routing_engine, "get_candidates", lambda model, max_tokens=None: routes)
    monkeypatch.setattr(
        app_module,
        "_adapter_registry",
        type("Registry", (), {"get": lambda self, provider_id: adapters[provider_id]})(),
    )


@pytest.mark.asyncio
async def test_stream_survives_beyond_request_deadline(app, monkeypatch):
    monkeypatch.setattr(app_module, "_REQUEST_DEADLINE_SECONDS", 0.05)

    async def slow_second_chunk():
        yield "data-1"
        await asyncio.sleep(0.3)
        yield "data-2"

    class Adapter:
        def chat_completion_stream(self, request, model):
            return slow_second_chunk()

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
    assert "data: data-1" in response.text
    assert "data: data-2" in response.text
    assert "data: [DONE]" in response.text
    assert "upstream_error" not in response.text


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
async def test_stream_client_abort_does_not_record_provider_failure(app, monkeypatch):
    async def two_chunks_slowly():
        yield "chunk-1"
        await asyncio.sleep(0.5)
        yield "chunk-2"

    class Adapter:
        def chat_completion_stream(self, request, model):
            return two_chunks_slowly()

    adapters = {"provider-1": Adapter()}

    async with lifespan(app):
        _single_route_stream_setup(monkeypatch, app, adapters)
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream(
                "POST",
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                headers={"Authorization": "Bearer test-key"},
            ) as response,
        ):
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line:
                    break

        engine = app_module._routing_engine
        assert engine is not None
        health = engine._health
        assert health is not None
        breaker = health.get_breaker("provider-1:model-1")
        assert breaker.failures == 0


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
        monkeypatch.setattr(app_module, "_adapter_registry", type("Registry", (), {"get": lambda self, _: CapturingAdapter()})())
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
