from __future__ import annotations

import httpx

from sparrow.errors import WARPUnavailableError
from sparrow.proxy import WARPProxy


def _build_warp_client(config: WARPProxy) -> httpx.AsyncClient:
    warp_transport = httpx.AsyncHTTPTransport(
        proxy=config.config.proxy_url,
        limits=httpx.Limits(
            max_connections=config.config.max_connections,
            max_keepalive_connections=config.config.max_keepalive,
        ),
    )
    return httpx.AsyncClient(
        transport=warp_transport,
        timeout=httpx.Timeout(
            connect=config.config.connect_timeout,
            read=config.config.read_timeout,
            write=config.config.connect_timeout,
            pool=config.config.connect_timeout,
        ),
        limits=httpx.Limits(
            max_connections=config.config.max_connections,
            max_keepalive_connections=config.config.max_keepalive,
        ),
        follow_redirects=True,
    )


def _build_direct_client(config: WARPProxy) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=config.config.connect_timeout,
            read=config.config.read_timeout,
            write=config.config.connect_timeout,
            pool=config.config.connect_timeout,
        ),
        limits=httpx.Limits(
            max_connections=config.config.max_connections,
            max_keepalive_connections=config.config.max_keepalive,
        ),
        follow_redirects=True,
    )


class SparrowClient:
    def __init__(self, warp_proxy: WARPProxy) -> None:
        self.warp = warp_proxy
        self._direct_client: httpx.AsyncClient | None = None
        self._warp_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        await self.warp.start()
        if self.warp.config.proxy_url:
            self._warp_client = _build_warp_client(self.warp)
        self._direct_client = _build_direct_client(self.warp)

    async def stop(self) -> None:
        warp_client = self._warp_client
        direct_client = self._direct_client
        self._warp_client = None
        self._direct_client = None

        try:
            if warp_client:
                await warp_client.aclose()
        finally:
            try:
                if direct_client:
                    await direct_client.aclose()
            finally:
                await self.warp.stop()

    def get_client(self, use_warp: bool = True, require_warp: bool = False) -> httpx.AsyncClient:
        if use_warp:
            if self._warp_client is not None and self.warp.is_warp_available():
                return self._warp_client
            if require_warp:
                raise WARPUnavailableError()
        if self._direct_client is not None:
            return self._direct_client
        raise RuntimeError("SparrowClient not started. Call start() first.")
