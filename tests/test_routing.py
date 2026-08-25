import pytest

from sparrow.config.aliases import AliasResolutionError, AliasResolver, ModelTarget
from sparrow.errors import AllProvidersExhaustedError
from sparrow.routing.engine import Route, RoutingEngine, RoutingMode


def test_register_route():
    engine = RoutingEngine()
    route = Route(provider_id="test", model_id="model-1", quality=8)
    engine.register_route(route)
    assert len(engine._routes) == 1


def test_select_fair():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", quality=5))
    engine.register_route(Route("p2", "m2", quality=8))
    engine.register_route(Route("p3", "m3", quality=6))

    r1 = engine.select("auto", RoutingMode.FAIR)
    r2 = engine.select("auto", RoutingMode.FAIR)
    r3 = engine.select("auto", RoutingMode.FAIR)

    assert r1.provider_id == "p1"
    assert r2.provider_id == "p2"
    assert r3.provider_id == "p3"


def test_select_quality():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", quality=5))
    engine.register_route(Route("p2", "m2", quality=9))
    engine.register_route(Route("p3", "m3", quality=6))

    route = engine.select("auto", RoutingMode.QUALITY)
    assert route.provider_id == "p2"


def test_select_specific_model():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", quality=5))
    engine.register_route(Route("p2", "m2", quality=8))

    route = engine.select("m2", RoutingMode.FAIR)
    assert route.model_id == "m2"


def test_all_providers_exhausted():
    engine = RoutingEngine()
    with pytest.raises(AllProvidersExhaustedError):
        engine.select("nonexistent-model")


def test_alias_resolver_resolves_direct_models_and_aliases():
    resolver = AliasResolver(
        aliases={"best": "p2/m2"},
        provider_models={"p1": {"m1"}, "p2": {"m2"}},
    )

    assert resolver.resolve("auto") == ModelTarget(provider_id=None, model_id="auto")
    assert resolver.resolve("m1") == ModelTarget(provider_id=None, model_id="m1")
    assert resolver.resolve("best") == ModelTarget(provider_id="p2", model_id="m2")


@pytest.mark.parametrize("model", ["unknown", "p1", "p1/"])
def test_alias_resolver_rejects_unknown_or_malformed_models(model):
    resolver = AliasResolver(
        aliases={"best": "p2/m2"},
        provider_models={"p1": {"m1"}, "p2": {"m2"}},
    )

    with pytest.raises(AliasResolutionError):
        resolver.resolve(model)


def test_auto_is_all_models_but_fair_is_an_explicit_model():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "fair"))
    engine.register_route(Route("p2", "other"))

    assert [(route.provider_id, route.model_id) for route in engine.get_candidates("auto")] == [
        ("p1", "fair"),
        ("p2", "other"),
    ]
    assert [(route.provider_id, route.model_id) for route in engine.get_candidates("fair")] == [("p1", "fair")]


def test_configured_routing_mode_orders_candidates():
    engine = RoutingEngine(mode=RoutingMode.QUALITY)
    engine.register_route(Route("slow", "shared", quality=2, avg_latency_ms=100))
    engine.register_route(Route("best", "shared", quality=9, avg_latency_ms=50))

    candidates = engine.ordered_candidates("shared")

    assert [route.provider_id for route in candidates] == ["best", "slow"]


def test_model_group_expands_candidates_across_providers():
    engine = RoutingEngine(model_groups={"hy3": ["hy3-free", "tencent/hy3:free"]})
    engine.register_route(Route("zen", "hy3-free"))
    engine.register_route(Route("kilo", "tencent/hy3:free"))
    engine.register_route(Route("ovh", "unrelated"))

    assert [(route.provider_id, route.model_id) for route in engine.get_candidates("tencent/hy3:free")] == [
        ("zen", "hy3-free"),
        ("kilo", "tencent/hy3:free"),
    ]
    assert [(route.provider_id, route.model_id) for route in engine.get_candidates("hy3-free")] == [
        ("zen", "hy3-free"),
        ("kilo", "tencent/hy3:free"),
    ]
    assert [route.provider_id for route in engine.get_candidates("unrelated")] == ["ovh"]


def test_provider_pin_blocks_group_expansion():
    engine = RoutingEngine(model_groups={"hy3": ["hy3-free", "tencent/hy3:free"]})
    engine.register_route(Route("zen", "hy3-free"))
    engine.register_route(Route("kilo", "tencent/hy3:free"))

    candidates = engine.get_candidates("tencent/hy3:free", provider_id="kilo")

    assert [route.provider_id for route in candidates] == ["kilo"]


def test_model_group_respects_health_and_quota_filters():
    from sparrow.routing.health import RouteHealthTracker

    health = RouteHealthTracker()
    engine = RoutingEngine(health_tracker=health, model_groups={"pair": ["a1", "b1"]})
    engine.register_route(Route("p1", "a1"))
    engine.register_route(Route("p2", "b1"))

    health.get_breaker("p1:a1").record_failure()
    health.get_breaker("p1:a1").record_failure()
    health.get_breaker("p1:a1").record_failure()
    health.get_breaker("p1:a1").record_failure()
    health.get_breaker("p1:a1").record_failure()

    assert [route.provider_id for route in engine.get_candidates("b1")] == ["p2"]


def test_model_groups_without_engine_are_ignored():
    engine = RoutingEngine()
    engine.register_route(Route("zen", "hy3-free"))
    engine.register_route(Route("kilo", "tencent/hy3:free"))

    assert [route.provider_id for route in engine.get_candidates("hy3-free")] == ["zen"]
