import httpx
import pytest

from sparrow.adapters.openai_compat import OpenAICompatAdapter
from sparrow.adapters.registry import AdapterRegistry
from sparrow.errors import UpstreamResponseError
from sparrow.middleware.auth import APIKeyAuth
from sparrow.models import ChatMessage, ChatRequest, EmbeddingRequest


class TestAdapterRegistry:
    def test_create_registry(self):
        registry = AdapterRegistry()
        assert len(registry.list_providers()) == 0

    def test_register_adapter(self):
        registry = AdapterRegistry()
        client = httpx.AsyncClient()
        registry.set_client(client)

        adapter = registry.register(
            provider_id="test-provider",
            provider_name="Test Provider",
            base_url="https://api.test.com/v1",
            models=[{"id": "model-1", "name": "Model 1", "enabled": True}],
        )
        assert isinstance(adapter, OpenAICompatAdapter)
        assert adapter.id == "test-provider"
        assert "model-1" in adapter.available_models

    def test_build_headers_includes_bearer_when_api_key_set(self):
        adapter = OpenAICompatAdapter(
            provider_id="bearer-provider",
            provider_name="Bearer Provider",
            base_url="https://api.bearer.test/v1",
            models=[{"id": "test-model", "name": "Test Model", "enabled": True}],
            client=httpx.AsyncClient(),
            api_key="free",
        )

        headers = adapter._build_headers()
        assert headers["Authorization"] == "Bearer free"

    def test_build_headers_omits_authorization_without_api_key(self):
        adapter = OpenAICompatAdapter(
            provider_id="anon",
            provider_name="Anon",
            base_url="https://api.anon.com/v1",
            models=[{"id": "m1", "name": "M1", "enabled": True}],
            client=httpx.AsyncClient(),
        )

        headers = adapter._build_headers()
        assert "Authorization" not in headers

    def test_get_adapter(self):
        registry = AdapterRegistry()
        client = httpx.AsyncClient()
        registry.set_client(client)

        registry.register(
            provider_id="p1",
            provider_name="P1",
            base_url="https://api.p1.com",
            models=[{"id": "m1", "name": "M1", "enabled": True}],
        )

        adapter = registry.get("p1")
        assert adapter is not None
        assert adapter.id == "p1"
        assert registry.get("nonexistent") is None

    def test_set_client_required(self):
        registry = AdapterRegistry()
        with pytest.raises(RuntimeError):
            registry.register("p1", "P1", "https://api.p1.com", [])


class TestAPIKeyAuth:
    def test_valid_key(self):
        auth = APIKeyAuth(key="key-1")
        assert auth.is_valid("key-1") is True

    def test_invalid_key(self):
        auth = APIKeyAuth(key="key-1")
        assert auth.is_valid("wrong-key") is False

    def test_none_key(self):
        auth = APIKeyAuth(key="key-1")
        assert auth.is_valid(None) is False

    def test_empty_key(self):
        auth = APIKeyAuth(key=None)
        assert auth.is_valid("anything") is False

    def test_set_keys(self):
        auth = APIKeyAuth(key="old-key")
        assert auth.is_valid("old-key") is True
        auth.set_keys("new-key")
        assert auth.is_valid("old-key") is False
        assert auth.is_valid("new-key") is True


@pytest.mark.asyncio
async def test_chat_completion_rejects_empty_choices_as_upstream_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    request = ChatRequest(model="model-1", messages=[ChatMessage(role="user", content="hello")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatAdapter(
            provider_id="provider-1",
            provider_name="Provider 1",
            base_url="https://api.test.com/v1",
            models=[{"id": "model-1", "name": "Model 1", "enabled": True}],
            client=client,
        )

        with pytest.raises(UpstreamResponseError):
            await adapter.chat_completion(request, "model-1")


@pytest.mark.asyncio
async def test_embedding_rejects_incomplete_data_as_upstream_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": "invalid"}]})

    request = EmbeddingRequest(model="model-1", input="hello")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatAdapter(
            provider_id="provider-1",
            provider_name="Provider 1",
            base_url="https://api.test.com/v1",
            models=[{"id": "model-1", "name": "Model 1", "enabled": True}],
            client=client,
        )

        with pytest.raises(UpstreamResponseError):
            await adapter.embedding(request, "model-1")


@pytest.mark.asyncio
async def test_chat_completion_stream_parses_sse_variants_and_closes_response():
    stream_state = {"closed": False}

    class TrackingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": keepalive\r\n\r\n"
            yield b'data:{"id":"first"}\r\n\r\n'
            yield b'data: \t{"usage":{"total_tokens":1}}\r\n\r\n'
            yield b"data:   [DONE]\r\n\r\n"

        async def aclose(self):
            stream_state["closed"] = True

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=TrackingStream(), request=request)

    request = ChatRequest(model="model-1", messages=[ChatMessage(role="user", content="hello")], stream=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatAdapter(
            provider_id="provider-1",
            provider_name="Provider 1",
            base_url="https://api.test.com/v1",
            models=[{"id": "model-1", "name": "Model 1", "enabled": True}],
            client=client,
        )
        chunks = [chunk async for chunk in adapter.chat_completion_stream(request, "model-1")]

    assert chunks == ['{"id":"first"}', '\t{"usage":{"total_tokens":1}}']
    assert stream_state["closed"] is True


@pytest.mark.asyncio
async def test_chat_completion_stream_forwards_extra_body():
    received_body = b""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_body
        received_body = request.content
        return httpx.Response(200, content=b"data: [DONE]\n\n", request=request)

    request = ChatRequest(
        model="model-1",
        messages=[ChatMessage(role="user", content="hello")],
        stream=True,
        extra_body={"reasoning_effort": "high"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatAdapter(
            provider_id="provider-1",
            provider_name="Provider 1",
            base_url="https://api.test.com/v1",
            models=[{"id": "model-1", "name": "Model 1", "enabled": True}],
            client=client,
        )
        chunks = [chunk async for chunk in adapter.chat_completion_stream(request, "model-1")]

    assert chunks == []
    assert b"reasoning_effort" in received_body
    assert b"high" in received_body


@pytest.mark.asyncio
async def test_build_headers_rotates_across_configured_keys():
    adapter = OpenAICompatAdapter(
        provider_id="nvidia",
        provider_name="NVIDIA",
        base_url="https://integrate.api.nvidia.com/v1",
        models=[{"id": "m1", "name": "M1", "enabled": True}],
        client=httpx.AsyncClient(),
        api_keys=["key-a", "key-b"],
    )

    headers_a = adapter._build_headers()
    headers_b = adapter._build_headers()
    assert headers_a["Authorization"] == "Bearer key-a"
    assert headers_b["Authorization"] == "Bearer key-b"


@pytest.mark.asyncio
async def test_chat_completion_stream_rotates_key_on_429():
    request_count = 0
    used_keys = []

    class RotatingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            used_keys.append(adapter._current_key())
            yield b'data:{"id":"first"}\r\n\r\n'
            yield b"data:   [DONE]\r\n\r\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count < 2:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, stream=RotatingStream(), request=request)

    request = ChatRequest(model="m1", messages=[ChatMessage(role="user", content="hello")], stream=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatAdapter(
            provider_id="nvidia",
            provider_name="NVIDIA",
            base_url="https://integrate.api.nvidia.com/v1",
            models=[{"id": "m1", "name": "M1", "enabled": True}],
            client=client,
            api_keys=["key-a", "key-b"],
        )
        chunks = [chunk async for chunk in adapter.chat_completion_stream(request, "m1")]

    assert chunks == ['{"id":"first"}']
    assert used_keys[0] == "key-b"
