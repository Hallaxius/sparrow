from __future__ import annotations

import logging
from typing import Any

from sparrow.plugins.base import Plugin

logger = logging.getLogger("sparrow")


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)
        logger.info("Registered plugin: %s", plugin.name)

    def unregister(self, name: str) -> None:
        self._plugins = [plugin for plugin in self._plugins if plugin.name != name]

    def get(self, name: str) -> Plugin | None:
        for plugin in self._plugins:
            if plugin.name == name:
                return plugin
        return None

    def list_plugins(self) -> list[Plugin]:
        return list(self._plugins)

    async def startup(self) -> None:
        for plugin in self._plugins:
            await plugin.on_startup()

    async def shutdown(self) -> None:
        for plugin in reversed(self._plugins):
            await plugin.on_shutdown()

    async def run_request_hooks(self, request: dict[str, Any]) -> dict[str, Any]:
        current = request
        for plugin in self._plugins:
            result = await plugin.on_request(current)
            if result is not None:
                current = result
        return current

    async def run_response_hooks(self, response: dict[str, Any]) -> dict[str, Any]:
        current = response
        for plugin in self._plugins:
            current = await plugin.on_response(current)
        return current
