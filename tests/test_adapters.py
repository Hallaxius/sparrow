import httpx
import pytest

from sparrow.adapters.openai_compat import OpenAICompatAdapter
from sparrow.adapters.registry import AdapterRegistry
from sparrow.middleware.auth import (
    APIKeyStore,
    _generate_api_key,
    _hash_key,
)


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


class TestAPIKeyStore:
    def test_create_key(self):
        store = APIKeyStore()
        key = store.create_key("test-key")
        assert key.startswith("sk-")
        assert len(key) == 51

    def test_validate_key(self):
        store = APIKeyStore()
        key = store.create_key("test-key")
        info = store.validate_key(key)
        assert info is not None
        assert info.name == "test-key"
        assert info.request_count == 1

    def test_validate_invalid_key(self):
        store = APIKeyStore()
        info = store.validate_key("sk-invalid")
        assert info is None

    def test_rate_limiting(self):
        store = APIKeyStore()
        key = store.create_key("rate-test", rate_limit=2, rate_window=60)
        info = store.validate_key(key)

        assert store.is_rate_limited(info.key_hash) == (False, 0.0)
        assert store.is_rate_limited(info.key_hash) == (False, 0.0)
        limited, retry = store.is_rate_limited(info.key_hash)
        assert limited is True
        assert retry > 0

    def test_list_keys(self):
        store = APIKeyStore()
        store.create_key("key-1")
        store.create_key("key-2")
        keys = store.list_keys()
        assert len(keys) == 2
        names = {k["name"] for k in keys}
        assert names == {"key-1", "key-2"}

    def test_delete_key(self):
        store = APIKeyStore()
        key = store.create_key("to-delete")
        info = store.validate_key(key)
        assert store.delete_key(info.key_hash) is True
        assert store.validate_key(key) is None

    def test_disable_key(self):
        store = APIKeyStore()
        key = store.create_key("to-disable")
        info = store.validate_key(key)
        assert store.disable_key(info.key_hash) is True
        assert store.validate_key(key) is None

    def test_hash_consistency(self):
        key = "sk-test-key-123"
        h1 = _hash_key(key)
        h2 = _hash_key(key)
        assert h1 == h2


class TestGenerateAPIKey:
    def test_format(self):
        key = _generate_api_key()
        assert key.startswith("sk-")
        assert len(key) == 51

    def test_uniqueness(self):
        keys = {_generate_api_key() for _ in range(100)}
        assert len(keys) == 100
