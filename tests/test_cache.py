from concurrent.futures import ThreadPoolExecutor

import pytest

from sparrow.cache import ResponseCache


def test_cache_key_isolated_by_scope_provider_and_model():
    cache = ResponseCache(ttl_seconds=60, max_size=10)
    body = {"messages": [{"content": "hello", "role": "user"}], "temperature": 0}

    cache.set("provider-a", "model-a", body, {"value": "a"}, scope="key-a")

    assert cache.get("provider-a", "model-a", body, scope="key-a") == {"value": "a"}
    assert cache.get("provider-a", "model-a", body, scope="key-b") is None
    assert cache.get("provider-b", "model-a", body, scope="key-a") is None
    assert cache.get("provider-a", "model-b", body, scope="key-a") is None


def test_cache_expiration_and_deterministic_eviction(monkeypatch):
    current_time = 100.0
    monkeypatch.setattr("sparrow.cache.time.monotonic", lambda: current_time)
    cache = ResponseCache(ttl_seconds=10, max_size=2)
    body = {"messages": []}

    cache.set("provider", "model-a", body, "a")
    cache.set("provider", "model-b", body, "b")
    cache.set("provider", "model-c", body, "c")

    assert cache.get("provider", "model-a", body) is None
    assert cache.get("provider", "model-b", body) == "b"
    assert cache.get("provider", "model-c", body) == "c"

    current_time = 111.0
    assert cache.get("provider", "model-b", body) is None
    assert cache.get("provider", "model-c", body) is None


def test_cache_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        ResponseCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        ResponseCache(max_size=0)


def test_cache_concurrent_writes_respect_max_size():
    cache = ResponseCache(ttl_seconds=60, max_size=4)
    body = {"messages": [{"content": "hello", "role": "user"}]}

    def write(index: int) -> None:
        cache.set("provider", f"model-{index}", body, index)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(32)))

    assert len(cache._cache) == 4
