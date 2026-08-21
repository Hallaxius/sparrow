import httpx
import pytest

from sparrow.adapters.openai_compat import OpenAICompatAdapter
from sparrow.adapters.registry import AdapterRegistry
from sparrow.middleware.auth import APIKeyAuth, _generate_api_key


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


class TestGenerateAPIKey:
    def test_format(self):
        key = _generate_api_key()
        assert key.startswith("sk-")
        assert len(key) == 51

    def test_uniqueness(self):
        keys = {_generate_api_key() for _ in range(100)}
        assert len(keys) == 100


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
