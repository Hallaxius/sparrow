from sparrow.routing.context_window import (
    CONTEXT_OVERFLOW_PATTERNS,
    ContextWindowLearner,
    is_context_overflow,
)


def test_broad_context_window_phrase_is_not_an_overflow():
    generic = "The context window is configurable in the admin panel."
    assert not is_context_overflow(generic)
    assert not any(pattern.search(generic) for pattern in CONTEXT_OVERFLOW_PATTERNS)


def test_specific_overflow_phrases_are_detected():
    assert is_context_overflow("context length exceeded for this request")
    assert is_context_overflow("maximum context length is 8192 tokens")
    assert is_context_overflow("Request too large: too many tokens")


def test_record_from_error_suppressed_for_small_max_tokens():
    learner = ContextWindowLearner()
    small_request = "context length exceeded"
    recorded = learner.record_from_error(
        provider_id="p1",
        model_id="m1",
        error_message=small_request,
        max_tokens=10,
        declared_limit=128000,
    )
    assert recorded is False
    assert learner.get_effective_limit("p1", "m1", 128000) == 128000


def test_record_from_error_applies_floor_for_large_max_tokens():
    learner = ContextWindowLearner()
    recorded = learner.record_from_error(
        provider_id="p1",
        model_id="m1",
        error_message="maximum context length exceeded",
        max_tokens=100000,
        declared_limit=128000,
    )
    assert recorded is True
    assert learner.get_effective_limit("p1", "m1", 128000) == int(100000 * 0.85)


def test_record_from_error_requires_overflow_message():
    learner = ContextWindowLearner()
    recorded = learner.record_from_error(
        provider_id="p1",
        model_id="m1",
        error_message="rate limit reached",
        max_tokens=100000,
        declared_limit=128000,
    )
    assert recorded is False
