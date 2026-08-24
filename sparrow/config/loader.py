from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from sparrow.config.models import Settings
from sparrow.errors import ConfigurationFileError
from sparrow.models.config import ProvidersConfig, ProvidersRuntime

PROJECT_ROOT = Path(__file__).parent.parent.parent


def load_config() -> Settings:
    return Settings()


def _resolve_config_path(settings: Settings) -> Path:
    config_file = settings.config_file
    if config_file.is_absolute():
        return config_file
    return PROJECT_ROOT / config_file


def _validation_reason(error: ValidationError) -> str:
    messages: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        messages.append(f"{location}: {item['msg']}")
    return "; ".join(messages)


def _load_json(path: Path) -> ProvidersConfig:
    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigurationFileError(str(path), "file does not exist") from error
    except PermissionError as error:
        raise ConfigurationFileError(str(path), "file is not readable") from error
    except json.JSONDecodeError as error:
        raise ConfigurationFileError(str(path), f"invalid JSON: {error}") from error

    models_path = path.parent / "models.json"
    if models_path.exists():
        try:
            raw_models = json.loads(models_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigurationFileError(str(models_path), f"invalid JSON: {error}") from error
    else:
        raw_models = {}

    if isinstance(raw_models, dict):
        for provider_id in raw_config.get("providers", {}):
            if provider_id in raw_models:
                raw_config["providers"][provider_id]["models"] = raw_models[provider_id]
            else:
                raw_config["providers"][provider_id]["models"] = []

    try:
        return ProvidersConfig.model_validate(raw_config)
    except ValidationError as error:
        raise ConfigurationFileError(str(path), _validation_reason(error)) from error


def load_all_providers() -> ProvidersRuntime:
    settings = load_config()
    config_path = _resolve_config_path(settings)
    return _load_json(config_path).to_runtime()
