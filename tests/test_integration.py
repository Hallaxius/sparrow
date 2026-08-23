from __future__ import annotations

import httpx

from sparrow.adapters.registry import AdapterRegistry
from sparrow.config.loader import load_providers_toml
from sparrow.routing.engine import Route, RoutingEngine, RoutingMode


def _build_registry_from_toml() -> tuple[AdapterRegistry, dict]:
    data = load_providers_toml()
    registry = AdapterRegistry()
    client = httpx.AsyncClient()
    registry.set_client(client)

    for provider_id, provider_data in data.get("providers", {}).items():
        registry.register(
            provider_id=provider_id,
            provider_name=provider_data.get("name", provider_id),
            base_url=provider_data.get("base_url", ""),
            models=provider_data.get("models", []),
        )

    return registry, data

def _build_routing_engine_from_toml() -> tuple[RoutingEngine, dict]:
    data = load_providers_toml()
    engine = RoutingEngine()

    for provider_id, provider_data in data.get("providers", {}).items():
        for model in provider_data.get("models", []):
            if model.get("enabled", True):
                route = Route(
                    provider_id=provider_id,
                    model_id=model["id"],
                    quality=model.get("quality", 5),
                    context_window=model.get("context", 128000),
                )
                engine.register_route(route)

    return engine, data

class TestTOMLIntegration:

    def test_providers_loaded(self):
        registry, _data = _build_registry_from_toml()
        providers = registry.list_providers()
        assert len(providers) == 5
        assert "ovhcloud" in providers
        assert "kilo" in providers
        assert "opencode" in providers
        assert "llm7" in providers
        assert "blockrun" in providers

    def test_adapter_types(self):
        from sparrow.adapters.openai_compat import OpenAICompatAdapter

        registry, _ = _build_registry_from_toml()
        for provider_id in registry.list_providers():
            adapter = registry.get(provider_id)
            assert adapter is not None
            assert isinstance(adapter, OpenAICompatAdapter)
            assert adapter.id == provider_id

    def test_adapter_base_urls(self):
        from sparrow.adapters.openai_compat import OpenAICompatAdapter

        registry, data = _build_registry_from_toml()
        for provider_id, provider_data in data.get("providers", {}).items():
            adapter = registry.get(provider_id)
            assert adapter is not None
            assert isinstance(adapter, OpenAICompatAdapter)
            expected_url = provider_data["base_url"].rstrip("/")
            assert adapter._base_url == expected_url

    def test_enabled_models_only(self):
        registry, data = _build_registry_from_toml()
        for provider_id, provider_data in data.get("providers", {}).items():
            adapter = registry.get(provider_id)
            assert adapter is not None
            expected_enabled = {
                m["id"]
                for m in provider_data.get("models", [])
                if m.get("enabled", True)
            }
            assert set(adapter.available_models) == expected_enabled

    def test_routing_engine_route_count(self):
        engine, data = _build_routing_engine_from_toml()
        expected_count = sum(
            1
            for pd in data.get("providers", {}).values()
            for m in pd.get("models", [])
            if m.get("enabled", True)
        )
        assert engine.route_count == expected_count

    def test_routing_select_per_model(self):
        engine, _data = _build_routing_engine_from_toml()

        route = engine.select("nvidia/nemotron-3-super-120b-a12b:free", RoutingMode.MODEL)
        assert route.provider_id == "kilo"
        assert route.model_id == "nvidia/nemotron-3-super-120b-a12b:free"

    def test_routing_select_auto_fair(self):
        engine, _ = _build_routing_engine_from_toml()
        route = engine.select("auto", RoutingMode.FAIR)
        assert route.provider_id != ""
        assert route.model_id != ""

    def test_routing_fair_round_robin(self):
        engine, _ = _build_routing_engine_from_toml()
        routes_seen = set()
        for _ in range(50):
            route = engine.select("auto", RoutingMode.FAIR)
            routes_seen.add(f"{route.provider_id}/{route.model_id}")
        assert len(routes_seen) > 1

    def test_routing_fast_picks_lowest_latency(self):
        engine = RoutingEngine()
        engine.register_route(Route("p1", "m1", avg_latency_ms=100))
        engine.register_route(Route("p2", "m1", avg_latency_ms=10))
        engine.register_route(Route("p3", "m1", avg_latency_ms=50))
        route = engine.select("m1", RoutingMode.FAST)
        assert route.provider_id == "p2"
        assert route.avg_latency_ms == 10

    def test_routing_quality_picks_highest(self):
        engine = RoutingEngine()
        engine.register_route(Route("p1", "m1", quality=3))
        engine.register_route(Route("p2", "m1", quality=9))
        engine.register_route(Route("p3", "m1", quality=6))
        route = engine.select("m1", RoutingMode.QUALITY)
        assert route.provider_id == "p2"
        assert route.quality == 9

    def test_all_providers_have_models(self):
        registry, _ = _build_registry_from_toml()
        for provider_id in registry.list_providers():
            adapter = registry.get(provider_id)
            assert adapter is not None
            # llm7 has no free models (all paid), skip it
            if provider_id == "llm7":
                continue
            assert len(adapter.available_models) > 0, (
                f"Provider {provider_id} has no enabled models"
            )
