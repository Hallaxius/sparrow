from __future__ import annotations

import logging

import httpx

from sparrow.proxy import WARPProxy

logger = logging.getLogger("sparrow.client")


class FallbackTransport(httpx.AsyncBaseTransport):

    def __init__(self, primary: httpx.AsyncBaseTransport, secondary: httpx.AsyncBaseTransport) -> None:
        self._primary = primary
        self._secondary = secondary

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._primary.handle_async_request(request)
        except httpx.TransportError:
            logger.debug("Primary transport failed, falling back to direct connection")
            return await self._secondary.handle_async_request(request)

    async def aclose(self) -> None:
        try:
            await self._primary.aclose()
        finally:
            await self._secondary.aclose()


def _build_warp_client(config: WARPProxy) -> httpx.AsyncClient:
    warp_transport = httpx.AsyncHTTPTransport(proxy=config.config.proxy_url)
    direct_transport = httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(
        transport=FallbackTransport(primary=warp_transport, secondary=direct_transport),
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


def _build_direct_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=True,
    )


class SparrowClient:

    def __init__(self, warp_proxy: WARPProxy | None = None) -> None:
        self.warp = warp_proxy or WARPProxy()
        self._direct_client: httpx.AsyncClient | None = None
        self._warp_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        await self.warp.start()
        self._direct_client = _build_direct_client()
        if self.warp.config.enabled and self.warp.config.proxy_url:
            self._warp_client = _build_warp_client(self.warp)

    async def stop(self) -> None:
        await self.warp.stop()
        if self._warp_client:
            await self._warp_client.aclose()
            self._warp_client = None
        if self._direct_client:
            await self._direct_client.aclose()
            self._direct_client = None

    def get_client(self, use_warp: bool = True) -> httpx.AsyncClient:
        if use_warp and self._warp_client is not None:
            return self._warp_client
        if self._direct_client is not None:
            return self._direct_client
        raise RuntimeError("SparrowClient not started. Call start() first.")
