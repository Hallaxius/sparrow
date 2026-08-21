from __future__ import annotations


class SparrowError(Exception):
    pass

class AllProvidersExhaustedError(SparrowError):

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"All providers exhausted for model: {model}")

class ProviderError(SparrowError):

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"Provider {provider}: {message}")

class RateLimitError(ProviderError):

    def __init__(self, provider: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(provider, f"Rate limited (retry_after={retry_after})")

class CircuitBreakerOpenError(ProviderError):

    def __init__(self, provider: str) -> None:
        super().__init__(provider, "Circuit breaker open")

class ConfigError(SparrowError):
    pass
