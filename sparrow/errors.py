from __future__ import annotations

from dataclasses import dataclass


class SparrowError(Exception):
    pass


class WARPUnavailableError(SparrowError):
    def __init__(self) -> None:
        super().__init__("Required WARP proxy is unavailable")


class AllProvidersExhaustedError(SparrowError):
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"All providers exhausted for model: {model}")


class ProviderError(SparrowError):
    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"Provider {provider}: {message}")


class UpstreamResponseError(ProviderError):
    def __init__(self, provider: str, resource: str) -> None:
        self.resource = resource
        super().__init__(provider, f"Invalid {resource} response")


class ConfigError(SparrowError):
    pass


@dataclass(frozen=True, slots=True)
class ConfigurationFileError(ConfigError):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"Invalid configuration at {self.path}: {self.reason}"
