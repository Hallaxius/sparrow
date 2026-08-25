from __future__ import annotations

from typing import TypedDict
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, field_validator, model_validator


class ProviderModelRuntime(TypedDict):
    id: str
    slug: str
    name: str
    quality: int
    context: int
    enabled: bool


class ProviderRuntime(TypedDict):
    name: str
    base_url: str
    adapter: str
    auth: str
    models: list[ProviderModelRuntime]
    daily_quota: int | None


class ProvidersRuntime(TypedDict):
    providers: dict[str, ProviderRuntime]
    aliases: dict[str, str]
    model_groups: dict[str, list[str]]


class ProviderModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    context: PositiveInt = 128000
    quality: int = Field(default=5, ge=1, le=10)
    enabled: bool = True

    def to_runtime(self) -> ProviderModelRuntime:
        return {
            "id": self.id,
            "slug": self.id,
            "name": self.name,
            "quality": self.quality,
            "context": self.context,
            "enabled": self.enabled,
        }


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    base_url: str
    adapter: str = "openai"
    auth: str = "none"
    models: list[ProviderModelConfig] = Field(default_factory=list)
    daily_quota: NonNegativeInt | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            message = "base_url must be an absolute HTTP URL"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def require_enabled_model(self) -> ProviderConfig:
        if not any(model.enabled for model in self.models):
            message = f"provider {self.name} must define at least one enabled model"
            raise ValueError(message)
        return self

    def to_runtime(self) -> ProviderRuntime:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "adapter": self.adapter,
            "auth": self.auth,
            "models": [model.to_runtime() for model in self.models],
            "daily_quota": self.daily_quota,
        }


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: dict[str, ProviderConfig]
    aliases: dict[str, str] = Field(default_factory=dict)
    model_groups: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_routes(self) -> ProvidersConfig:
        if not self.providers:
            message = "configuration must define at least one provider"
            raise ValueError(message)
        for provider_id in self.providers:
            if not provider_id.strip() or "/" in provider_id:
                message = f"provider id {provider_id!r} must be non-empty and must not contain '/'"
                raise ValueError(message)
        for alias, target in self.aliases.items():
            if not alias.strip():
                message = "alias id must be non-empty"
                raise ValueError(message)
            provider_id, separator, model_id = target.partition("/")
            if separator == "" or provider_id not in self.providers or not model_id:
                message = f"alias {alias!r} must target an existing provider/model as provider_id/model_id"
                raise ValueError(message)
            model_ids = {model.id for model in self.providers[provider_id].models}
            if model_id not in model_ids:
                message = f"alias {alias!r} targets unknown model {target!r}"
                raise ValueError(message)
        all_model_ids = {model.id for provider in self.providers.values() for model in provider.models}
        for group_name, members in self.model_groups.items():
            if not group_name.strip():
                message = "model group name must be non-empty"
                raise ValueError(message)
            if len(set(members)) < 2:
                message = f"model group {group_name!r} must contain at least two distinct models"
                raise ValueError(message)
            unknown = [member for member in members if member not in all_model_ids]
            if unknown:
                message = f"model group {group_name!r} references unknown models: {unknown}"
                raise ValueError(message)
        return self

    def to_runtime(self) -> ProvidersRuntime:
        return {
            "providers": {provider_id: provider.to_runtime() for provider_id, provider in self.providers.items()},
            "aliases": dict(self.aliases),
            "model_groups": {name: list(members) for name, members in self.model_groups.items()},
        }
