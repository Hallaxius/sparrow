from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

from sparrow.proxy import WARPProxy

logger = logging.getLogger("sparrow.init")

PROJECT_ROOT = Path(__file__).parent.parent
PROVIDERS_JSON_PATH = PROJECT_ROOT / "providers.json"
MODELS_JSON_PATH = PROJECT_ROOT / "models.json"

DEFAULT_QUALITY = 5
DEFAULT_CONTEXT = 128000
DEFAULT_ENABLED = True

MODELS_ENDPOINT = "/models"


# Keywords to identify non-chat models (embeddings, TTS, image generation, etc.)
NON_CHAT_KEYWORDS = [
    "embedding", "embed", "tts", "text-to-speech", "speech", "audio",
    "whisper", "transcribe", "translation", "vision", "image", "diffusion",
    "stable-diffusion", "dalle", "midjourney", "moderation", "classifier",
    "rerank", "bge-", "e5-", "gte-", "jina-", "nomic-", "mxbai-", "instructor-",
]

def is_chat_model(model: dict[str, Any]) -> bool:
    """Check if a model is likely a chat/completion model (not embedding, TTS, etc.)."""
    model_id = str(model.get("id", "")).lower()
    model_name = str(model.get("name", "")).lower()
    model_desc = str(model.get("description", "")).lower()

    text = f"{model_id} {model_name} {model_desc}"

    for keyword in NON_CHAT_KEYWORDS:
        if keyword in text:
            return False

    # Check capabilities if available and non-empty
    caps = model.get("capabilities")
    if isinstance(caps, list) and caps:
        chat_caps = {"chat", "completion", "text-generation", "conversation"}
        if not any(c in chat_caps for c in caps):
            # Has capabilities but none are chat-related
            return False

    # Check modalities at top level if present and non-empty
    modalities = model.get("modalities")
    if isinstance(modalities, dict) and modalities:
        input_mods = modalities.get("input", [])
        output_mods = modalities.get("output", [])
        if input_mods and "text" not in input_mods:
            return False
        if output_mods and "text" not in output_mods:
            return False

    # Check architecture field (used by Kilo and others)
    arch = model.get("architecture")
    if isinstance(arch, dict) and arch:
        input_mods = arch.get("input_modalities", [])
        output_mods = arch.get("output_modalities", [])
        if input_mods and "text" not in input_mods:
            return False
        if output_mods and "text" not in output_mods:
            return False

    return True


def _get_pricing(model: dict[str, Any]) -> dict[str, Any]:
    pricing = model.get("pricing")
    if isinstance(pricing, dict):
        return pricing
    return {}


def is_model_free(provider_id: str, model: dict[str, Any]) -> bool:
    if provider_id == "ovhcloud":
        pricing = _get_pricing(model)
        prompt = str(pricing.get("prompt", "1"))
        completion = str(pricing.get("completion", "1"))
        try:
            return float(prompt) == 0.0 and float(completion) == 0.0
        except (ValueError, TypeError):
            return prompt == "0" and completion == "0"

    if provider_id == "kilo":
        is_free = model.get("isFree")
        if is_free is True:
            return True
        pricing = _get_pricing(model)
        prompt = str(pricing.get("prompt", "1"))
        completion = str(pricing.get("completion", "1"))
        return prompt == "0" and completion == "0"

    if provider_id == "opencode":
        model_id = str(model.get("id", ""))
        return model_id.endswith("-free")

    if provider_id == "llm7":
        pricing = _get_pricing(model)
        input_price = pricing.get("input", 1)
        output_price = pricing.get("output", 1)
        try:
            return float(input_price) == 0.0 and float(output_price) == 0.0
        except (ValueError, TypeError):
            return False

    if provider_id == "blockrun":
        billing_mode = model.get("billing_mode")
        if billing_mode == "free":
            return True
        pricing = _get_pricing(model)
        input_price = pricing.get("input", 1)
        output_price = pricing.get("output", 1)
        try:
            return float(input_price) == 0.0 and float(output_price) == 0.0
        except (ValueError, TypeError):
            return False

    return False



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

        # First pass: filter to chat models only (skip embeddings, TTS, image, etc.)
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


def load_existing_providers() -> dict[str, Any]:
    providers_list: list[dict[str, Any]] = []
    models_list: list[dict[str, Any]] = []

    if PROVIDERS_JSON_PATH.exists():
        with open(PROVIDERS_JSON_PATH, encoding="utf-8") as f:
            providers_list = json.load(f)

    if MODELS_JSON_PATH.exists():
        with open(MODELS_JSON_PATH, encoding="utf-8") as f:
            models_list = json.load(f)

    providers_by_id = {p["id"]: p for p in providers_list if "id" in p}
    models_by_provider: dict[str, list[dict[str, Any]]] = {}
    for m in models_list:
        pid = m.get("provider_id", "")
        if pid not in models_by_provider:
            models_by_provider[pid] = []
        models_by_provider[pid].append(m)

    result_providers: dict[str, Any] = {}
    for pid, pdata in providers_by_id.items():
        result_providers[pid] = {
            "name": pdata.get("name", pid),
            "base_url": pdata.get("base_url", ""),
            "models": models_by_provider.get(pid, []),
        }

    if result_providers:
        return {"providers": result_providers}

    return {}


def merge_models(
    existing_models: list[dict[str, Any]], fetched_models: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    existing_by_id = {m["id"]: m for m in existing_models if "id" in m}
    fetched_by_id = {m["id"]: m for m in fetched_models if "id" in m}

    merged = []

    for model_id, existing in existing_by_id.items():
        if model_id in fetched_by_id:
            merged_model = {
                "id": model_id,
                "name": existing.get("name", model_id),
                "context": existing.get("context", DEFAULT_CONTEXT),
                "quality": existing.get("quality", DEFAULT_QUALITY),
                "enabled": existing.get("enabled", DEFAULT_ENABLED),
            }
            merged.append(merged_model)
        else:
            pass

    for model_id, _fetched in fetched_by_id.items():
        if model_id not in existing_by_id:
            merged_model = {
                "id": model_id,
                "name": model_id,
                "context": DEFAULT_CONTEXT,
                "quality": DEFAULT_QUALITY,
                "enabled": DEFAULT_ENABLED,
            }
            merged.append(merged_model)

    return merged


async def run_init() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    providers_data = load_existing_providers()
    providers = providers_data.get("providers", {})

    if not providers:
        logger.info("No providers found, nothing to update")
        return 0

    warp = WARPProxy()
    await warp.start()

    if warp.is_warp_available():
        logger.info("WARP proxy available, using proxy for model fetching")
        client = warp.get_client(use_proxy=True)
    else:
        logger.warning("WARP proxy not available, using direct connection for model fetching")
        client = warp.get_client(use_proxy=False)

    total_fetched = 0
    total_merged = 0

    for provider_id, provider_data in providers.items():
        base_url = provider_data.get("base_url", "")
        if not base_url:
            logger.warning("Provider %s has no base_url, skipping", provider_id)
            continue

        existing_models = provider_data.get("models", [])
        fetched_models = await fetch_models_from_provider(provider_id, base_url, client)

        merged_models = merge_models(existing_models, fetched_models)
        provider_data["models"] = merged_models
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
            logger.info("Provider %s: no free models fetched, filtered to %d models", provider_id, len(merged_models))

    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()

    providers_output = []
    for pid, pdata in providers.items():
        providers_output.append({
            "id": pid,
            "name": pdata.get("name", pid),
            "base_url": pdata.get("base_url", ""),
            "created_at": now,
        })

    models_output = []
    for pid, pdata in providers.items():
        for model in pdata.get("models", []):
            models_output.append({
                "id": model.get("id", ""),
                "model": model.get("name", model.get("id", "")),
                "slug": model.get("id", ""),
                "provider_id": pid,
                "response_time": 0.0,
                "created_at": now,
            })

    with open(PROVIDERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(providers_output, f, indent=2, ensure_ascii=False)

    with open(MODELS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(models_output, f, indent=2, ensure_ascii=False)

    logger.info(
        "Updated providers.json and models.json: %d providers, %d total models",
        len(providers),
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
