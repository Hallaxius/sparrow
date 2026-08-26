from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from sparrow.config.loader import _config_path, _load_json, load_config
from sparrow.config.models import Settings
from sparrow.models.config import ProviderConfig, ProviderModelConfig, ProvidersConfig
from sparrow.proxy import WARPConfig, WARPProxy

logger = logging.getLogger("sparrow.init")

DEFAULT_QUALITY = 5
DEFAULT_CONTEXT = 128000
DEFAULT_ENABLED = True

MODELS_ENDPOINT = "/models"


NON_CHAT_KEYWORDS = [
    "embedding",
    "embed",
    "tts",
    "text-to-speech",
    "speech",
    "audio",
    "whisper",
    "transcribe",
    "translation",
    "vision",
    "image",
    "diffusion",
    "stable-diffusion",
    "dalle",
    "midjourney",
    "moderation",
    "classifier",
    "rerank",
    "bge-",
    "e5-",
    "gte-",
    "jina-",
    "nomic-",
    "mxbai-",
    "instructor-",
]


def is_chat_model(model: dict[str, Any]) -> bool:
    model_id = str(model.get("id", "")).lower()
    model_name = str(model.get("name", "")).lower()
    model_desc = str(model.get("description", "")).lower()

    text = f"{model_id} {model_name} {model_desc}"

    for keyword in NON_CHAT_KEYWORDS:
        if keyword in text:
            return False

    caps = model.get("capabilities")
    if isinstance(caps, list) and caps:
        chat_caps = {"chat", "completion", "text-generation", "conversation"}
        if not any(c in chat_caps for c in caps):
            return False

    modalities = model.get("modalities")
    if isinstance(modalities, dict) and modalities:
        input_mods = modalities.get("input", [])
        output_mods = modalities.get("output", [])
        if input_mods and "text" not in input_mods:
            return False
        if output_mods and "text" not in output_mods:
            return False

    arch = model.get("architecture")
    if isinstance(arch, dict) and arch:
        input_mods = arch.get("input_modalities", [])
        output_mods = arch.get("output_modalities", [])
        if input_mods and "text" not in input_mods:
            return False
        if output_mods and "text" not in output_mods:
            return False

    return True


async def fetch_models_from_provider(
    provider_id: str, base_url: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    url = base_url.rstrip("/") + MODELS_ENDPOINT
    try:
        logger.info("Fetching models from %s", url)
        resp = await client.get(url, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(
                "Provider %s returned status %d, keeping existing models",
                base_url,
                resp.status_code,
            )
            return []
        data = resp.json()
        models = data.get("data", [])
        if not isinstance(models, list):
            logger.warning(
                "Provider %s returned invalid models format, keeping existing models",
                base_url,
            )
            return []

        candidate_models = []
        for m in models:
            if not isinstance(m, dict):
                continue
            model_id = m.get("id")
            if not model_id:
                logger.debug("Skipping model without id: %s", m)
                continue
            if not is_chat_model(m):
                logger.debug("Skipping non-chat model %s from %s", model_id, provider_id)
                continue
            candidate_models.append(model_id)

        logger.info("Found %d candidate chat models from %s", len(candidate_models), base_url)

        valid_models = [{"id": mid} for mid in candidate_models]
        logger.info("Returning %d chat models from %s", len(valid_models), base_url)
        return valid_models
    except httpx.TimeoutException:
        logger.warning("Timeout fetching models from %s, keeping existing models", base_url)
        return []
    except httpx.HTTPError as e:
        logger.warning(
            "HTTP error fetching models from %s: %s, keeping existing models",
            base_url,
            e,
        )
        return []
    except Exception as e:
        logger.warning(
            "Unexpected error fetching models from %s: %s, keeping existing models",
            base_url,
            e,
        )
        return []


def load_existing_config() -> tuple[Settings, Path, ProvidersConfig]:
    settings = load_config()
    config_path = _config_path()
    return settings, config_path, _load_json(config_path)


def write_text_atomic(path: Path, content: str) -> None:
    directory = path.parent
    descriptor, temporary_name = tempfile.mkstemp(
        dir=directory,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        if os.name == "posix":
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def providers_config_to_providers_json(config: ProvidersConfig) -> str:
    providers_data: dict[str, dict[str, Any]] = {}
    for provider_id, provider in config.providers.items():
        entry: dict[str, Any] = {
            "name": provider.name,
            "base_url": provider.base_url,
            "adapter": provider.adapter,
            "auth": provider.auth,
        }
        if provider.daily_quota is not None:
            entry["daily_quota"] = provider.daily_quota
        if provider.api_key is not None:
            entry["api_key"] = provider.api_key
        if provider.api_keys:
            entry["api_keys"] = provider.api_keys
        providers_data[provider_id] = entry

    output: dict[str, Any] = {
        "providers": providers_data,
        "aliases": dict(config.aliases),
    }
    return json.dumps(output, indent=2, ensure_ascii=False) + "\n"


def providers_config_to_models_json(config: ProvidersConfig) -> str:
    models_data: dict[str, list[dict[str, Any]]] = {}
    for provider_id, provider in config.providers.items():
        models_data[provider_id] = [
            {
                "id": model.id,
                "name": model.name,
                "context": model.context,
                "quality": model.quality,
                "enabled": model.enabled,
            }
            for model in provider.models
        ]
    return json.dumps(models_data, indent=2, ensure_ascii=False) + "\n"


def write_providers_config(path: Path, config: ProvidersConfig) -> None:
    write_text_atomic(path, providers_config_to_providers_json(config))
    models_path = path.parent / "models.json"
    write_text_atomic(models_path, providers_config_to_models_json(config))


def merge_models(
    existing_models: list[ProviderModelConfig], fetched_models: list[dict[str, Any]]
) -> list[ProviderModelConfig]:
    fetched_ids = [str(model["id"]) for model in fetched_models if "id" in model]
    if not fetched_ids:
        return existing_models

    existing_ids = {model.id for model in existing_models}
    merged = list(existing_models)

    for model_id in fetched_ids:
        if model_id not in existing_ids:
            merged.append(
                ProviderModelConfig(
                    id=model_id,
                    name=model_id,
                    context=DEFAULT_CONTEXT,
                    quality=DEFAULT_QUALITY,
                    enabled=DEFAULT_ENABLED,
                )
            )
            existing_ids.add(model_id)

    return merged


async def run_init() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings, config_path, providers_config = load_existing_config()
    providers = providers_config.providers

    warp = WARPProxy(WARPConfig.from_settings(settings))
    try:
        await warp.start()
        client = warp.get_client(use_proxy=True)

        total_fetched = 0
        total_merged = 0
        updated_providers: dict[str, ProviderConfig] = {}

        for provider_id, provider in providers.items():
            fetched_models = await fetch_models_from_provider(provider_id, provider.base_url, client)
            merged_models = merge_models(provider.models, fetched_models)
            updated_providers[provider_id] = provider.model_copy(update={"models": merged_models})
            total_fetched += len(fetched_models)
            total_merged += len(merged_models)
            if fetched_models:
                logger.info(
                    "Provider %s: %d fetched, %d merged",
                    provider_id,
                    len(fetched_models),
                    len(merged_models),
                )
            else:
                logger.info(
                    "Provider %s: no models fetched, keeping %d existing models", provider_id, len(merged_models)
                )

        updated_config = ProvidersConfig(providers=updated_providers, aliases=providers_config.aliases)
        write_providers_config(config_path, updated_config)
    finally:
        await warp.stop()

    logger.info(
        "Updated configuration %s: %d providers, %d fetched models, %d total models",
        config_path,
        len(providers),
        total_fetched,
        total_merged,
    )
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(run_init())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception("Init failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
