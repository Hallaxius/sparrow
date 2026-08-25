from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def on_startup(self) -> None: ...

    async def on_shutdown(self) -> None: ...

    async def on_request(self, request: dict[str, Any]) -> dict[str, Any] | None: ...

    async def on_response(self, response: dict[str, Any]) -> dict[str, Any]: ...
