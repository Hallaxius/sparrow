import pytest

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
