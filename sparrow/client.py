from __future__ import annotations

import httpx

from sparrow.proxy import WARPProxy


class SparrowClient:

    def __init__(self, warp_proxy: WARPProxy | None = None) -> None:
        self.warp = warp_proxy or WARPProxy()
        self._direct_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        await self.warp.start()
        self._direct_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=10.0,
                pool=10.0,
            ),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
            follow_redirects=True,
        )

    async def stop(self) -> None:
        await self.warp.stop()
        if self._direct_client:
            await self._direct_client.aclose()
            self._direct_client = None

    def get_client(self, use_warp: bool = True) -> httpx.AsyncClient:
        if use_warp and self.warp.config.enabled:
            return self.warp.get_client(use_proxy=True)
        if self._direct_client is not None:
            return self._direct_client
        raise RuntimeError("SparrowClient not started. Call start() first.")
