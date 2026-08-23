from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sparrow.config.models import Settings

PROJECT_ROOT = Path(__file__).parent.parent.parent

DEFAULT_QUALITY = 5
DEFAULT_CONTEXT = 128000


def load_config() -> Settings:
    return Settings()


def load_providers_json() -> list[dict[str, Any]]:
    config_path = PROJECT_ROOT / "providers.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            result: list[dict[str, Any]] = json.load(f)
            return result
    return []


def load_models_json() -> list[dict[str, Any]]:
    config_path = PROJECT_ROOT / "models.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            result: list[dict[str, Any]] = json.load(f)
            return result
    return []


def _build_provider_models_map(models: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for model in models:
        provider_id = model.get("provider_id", "")
        if provider_id not in result:
            result[provider_id] = []
        result[provider_id].append({
            "id": model.get("slug", model.get("model", "")),
            "name": model.get("model", ""),
            "quality": DEFAULT_QUALITY,
            "context": DEFAULT_CONTEXT,
            "enabled": True,
        })
    return result


def load_all_providers() -> dict[str, Any]:
    providers_list = load_providers_json()
    models_list = load_models_json()

    models_by_provider = _build_provider_models_map(models_list)

    providers: dict[str, Any] = {}
    for provider in providers_list:
        provider_id = provider.get("id", "")
        providers[provider_id] = {
            "name": provider.get("name", ""),
            "base_url": provider.get("base_url", ""),
            "adapter": "openai",
            "auth": "none",
            "models": models_by_provider.get(provider_id, []),
        }

    return {"providers": providers}
