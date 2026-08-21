from __future__ import annotations

import asyncio
import logging
import sys
import tomllib
from pathlib import Path
from typing import Any

import httpx
import tomli_w

from sparrow.proxy import WARPProxy

logger = logging.getLogger("sparrow.init")

PROVIDERS_TOML_PATH = Path(__file__).parent.parent / "providers.toml"

DEFAULT_QUALITY = 5
DEFAULT_CONTEXT = 128000
DEFAULT_ENABLED = True

MODELS_ENDPOINT = "/models"

# Hardcoded seed configuration for providers when providers.toml doesn't exist
SEED_PROVIDERS = {
    "ovhcloud": {
        "name": "OVHcloud",
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
    "kilo": {
        "name": "Kilo Gateway",
        "base_url": "https://api.kilo.ai/api/gateway",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
    "opencode": {
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
    "llm7": {
        "name": "LLM7",
        "base_url": "https://api.llm7.io/v1",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
    "blockrun": {
        "name": "BlockRun",
        "base_url": "https://blockrun.ai/api/v1",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
    "algoholia": {
        "name": "Algoholia",
        "base_url": "https://algoholia.com/api/free-llm/v1",
        "adapter": "openai",
        "auth": "none",
        "models": [],
    },
}

SEED_ALIASES = {
    "gpt-4o": "kilo/nvidia/nemotron-3-super-120b-a12b:free",
    "gpt-4o-mini": "kilo/openrouter/free",
    "claude-3.5-sonnet": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "claude-3-haiku": "opencode/mimo-v2.5-free",
    "deepseek-r1": "opencode/deepseek-v4-flash-free",
    "gemini-2.5-flash": "opencode/deepseek-v4-flash-free",
    "mistral-small": "ovhcloud/Mistral-Small-3.2-24B-Instruct-2506",
    "auto": "fair",
}


async def fetch_models_from_provider(
    base_url: str, client: httpx.AsyncClient
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
        valid_models = []
        for m in models:
            if not isinstance(m, dict):
                continue
            model_id = m.get("id")
            if not model_id:
                logger.debug("Skipping model without id: %s", m)
                continue
            valid_models.append({"id": model_id})
        logger.info("Fetched %d models from %s", len(valid_models), base_url)
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
    if PROVIDERS_TOML_PATH.exists():
        with open(PROVIDERS_TOML_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


def get_seed_config() -> dict[str, Any]:
    """Return the hardcoded seed configuration for providers and aliases."""
    return {"providers": SEED_PROVIDERS, "aliases": SEED_ALIASES}


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
            merged.append(existing)

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
    aliases = providers_data.get("aliases", {})

    # If no providers configured, use seed configuration
    if not providers:
        logger.info("No providers.toml found, using seed configuration")
        seed = get_seed_config()
        providers = seed["providers"]
        aliases = seed["aliases"]

    warp = WARPProxy()
    await warp.start()

    try:
        client = warp.get_client(use_proxy=True)
    except RuntimeError:
        logger.warning("WARP proxy not available, using direct connection")
        client = warp.get_client(use_proxy=False)

    total_fetched = 0
    total_merged = 0

    for provider_id, provider_data in providers.items():
        base_url = provider_data.get("base_url", "")
        if not base_url:
            logger.warning("Provider %s has no base_url, skipping", provider_id)
            continue

        existing_models = provider_data.get("models", [])
        fetched_models = await fetch_models_from_provider(base_url, client)

        if fetched_models:
            merged_models = merge_models(existing_models, fetched_models)
            provider_data["models"] = merged_models
            total_fetched += len(fetched_models)
            total_merged += len(merged_models)
            logger.info(
                "Provider %s: %d fetched, %d merged",
                provider_id,
                len(fetched_models),
                len(merged_models),
            )
        else:
            logger.info("Provider %s: no models fetched, keeping existing", provider_id)

    output_data = {"providers": providers, "aliases": aliases}

    with open(PROVIDERS_TOML_PATH, "wb") as f:
        tomli_w.dump(output_data, f)

    logger.info(
        "Updated providers.toml: %d providers, %d total models",
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
