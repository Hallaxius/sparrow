from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass


class AliasResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelTarget:
    provider_id: str | None
    model_id: str


class AliasResolver:
    def __init__(self, aliases: Mapping[str, str], provider_models: Mapping[str, Collection[str]]) -> None:
        self._aliases = dict(aliases)
        self._provider_models = {provider_id: set(models) for provider_id, models in provider_models.items()}
        self._models = {model_id for models in self._provider_models.values() for model_id in models}

    def resolve(self, model: str) -> ModelTarget:
        if model == "auto":
            return ModelTarget(provider_id=None, model_id="auto")

        if model in self._aliases:
            return self._resolve_alias(model, self._aliases[model])

        if model in self._models:
            return ModelTarget(provider_id=None, model_id=model)

        raise AliasResolutionError(f"unknown model or alias: {model}")

    def _resolve_alias(self, alias: str, target: str) -> ModelTarget:
        provider_id, separator, model_id = target.partition("/")
        if separator == "" or not provider_id or not model_id:
            raise AliasResolutionError(f"alias {alias} has an invalid target")
        if provider_id not in self._provider_models or model_id not in self._provider_models[provider_id]:
            raise AliasResolutionError(f"alias {alias} targets an unknown model")
        return ModelTarget(provider_id=provider_id, model_id=model_id)
