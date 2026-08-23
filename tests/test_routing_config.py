
from sparrow.routing.engine import Route, RoutingEngine
from sparrow.routing.quota import QuotaTracker


class LimitedQuotaTracker(QuotaTracker):

    def __init__(self, limit: int = 2) -> None:
        super().__init__()
        self._limit = limit

    def can_request(self, provider_id: str, model: str, limit: int = -1) -> bool:
        if limit != -1:
            return super().can_request(provider_id, model, limit)
        return super().can_request(provider_id, model, self._limit)

def test_quota_exhaustion_filters_candidates():
    quota = LimitedQuotaTracker(limit=2)
    engine = RoutingEngine(quota=quota)
    engine.register_route(Route("provider_a", "model_a"))
    engine.register_route(Route("provider_b", "model_b"))

    quota.record("provider_a", "model_a")
    quota.record("provider_a", "model_a")

    candidates = engine.get_candidates("auto")
    provider_ids = [r.provider_id for r in candidates]

    assert "provider_a" not in provider_ids
    assert "provider_b" in provider_ids

def test_quota_select_skips_exhausted_provider():
    quota = LimitedQuotaTracker(limit=1)
    engine = RoutingEngine(quota=quota)
    engine.register_route(Route("provider_a", "model_a"))
    engine.register_route(Route("provider_b", "model_b"))

    quota.record("provider_a", "model_a")

    route = engine.select("auto")
    assert route.provider_id == "provider_b"

def test_quota_partial_exhaustion():
    quota = LimitedQuotaTracker(limit=2)
    engine = RoutingEngine(quota=quota)
    engine.register_route(Route("provider_a", "model_a"))

    quota.record("provider_a", "model_a")

    candidates = engine.get_candidates("auto")
    assert len(candidates) == 1
    assert candidates[0].provider_id == "provider_a"

def test_no_quota_all_providers_pass():
    engine = RoutingEngine()
    engine.register_route(Route("provider_a", "model_a"))
    engine.register_route(Route("provider_b", "model_b"))

    candidates = engine.get_candidates("auto")
    assert len(candidates) == 2

def test_max_tokens_filters_small_context():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", context_window=4096))
    engine.register_route(Route("p2", "m2", context_window=128000))

    candidates = engine.get_candidates("auto", max_tokens=8192)
    assert len(candidates) == 1
    assert candidates[0].context_window == 128000

def test_max_tokens_exact_match():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", context_window=4096))
    engine.register_route(Route("p2", "m2", context_window=128000))

    candidates = engine.get_candidates("auto", max_tokens=128000)
    assert len(candidates) == 1
    assert candidates[0].context_window == 128000

def test_max_tokens_none_returns_all():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", context_window=4096))
    engine.register_route(Route("p2", "m2", context_window=128000))

    candidates = engine.get_candidates("auto", max_tokens=None)
    assert len(candidates) == 2

def test_max_tokens_all_filtered():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", context_window=4096))
    engine.register_route(Route("p2", "m2", context_window=128000))

    candidates = engine.get_candidates("auto", max_tokens=256000)
    assert len(candidates) == 0

def test_max_tokens_specific_model():
    engine = RoutingEngine()
    engine.register_route(Route("p1", "m1", context_window=4096))
    engine.register_route(Route("p2", "m1", context_window=128000))

    candidates = engine.get_candidates("m1", max_tokens=8192)
    assert len(candidates) == 1
    assert candidates[0].provider_id == "p2"

