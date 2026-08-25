from __future__ import annotations

import logging

import httpx

from sparrow.adapters.base import ProviderAdapter
from sparrow.models.config import ProviderModelRuntime

logger = logging.getLogger("sparrow.adapters")


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        self._client: httpx.AsyncClient | None = None

    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def register(
        self,
        provider_id: str,
        provider_name: str,
        base_url: str,
        models: list[ProviderModelRuntime],
        api_key: str | None = None,
        api_keys: list[str] | None = None,
    ) -> ProviderAdapter:
        if self._client is None:
            raise RuntimeError("AdapterRegistry client not initialized. Call set_client() first.")

        from sparrow.adapters.openai_compat import OpenAICompatAdapter

        adapter = OpenAICompatAdapter(
            provider_id=provider_id,
            provider_name=provider_name,
            base_url=base_url,
            models=models,
            client=self._client,
            api_key=api_key,
            api_keys=api_keys,
        )
        self._adapters[provider_id] = adapter
        logger.info("Registered adapter: %s (%s)", provider_id, base_url)
        return adapter

    def get(self, provider_id: str) -> ProviderAdapter | None:
        return self._adapters.get(provider_id)

    def get_all(self) -> dict[str, ProviderAdapter]:
        return self._adapters.copy()

    def list_providers(self) -> list[str]:
        return list(self._adapters.keys())
