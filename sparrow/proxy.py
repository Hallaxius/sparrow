from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("sparrow.proxy")

@dataclass
class WARPConfig:
    enabled: bool = True
    proxy_url: str = "socks5://warp:1080"
    http_proxy_url: str = ""
    health_check_url: str = "https://cloudflare.com/cdn-cgi/trace"
    health_check_interval: int = 60
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    max_connections: int = 100
    max_keepalive: int = 20

    @classmethod
    def from_env(cls) -> WARPConfig:
        enabled_raw = os.getenv("SPARROW_WARP_ENABLED", "").lower()
        enabled = enabled_raw not in ("0", "false", "no")
        proxy_url = os.getenv("SPARROW_WARP_URL", "socks5://warp:1080")
        return cls(
            enabled=enabled,
            proxy_url=proxy_url,
            http_proxy_url=os.getenv("SPARROW_WARP_HTTP_URL", ""),
            health_check_interval=int(os.getenv("WARP_HEALTH_INTERVAL", "60")),
            connect_timeout=float(os.getenv("WARP_CONNECT_TIMEOUT", "10")),
            read_timeout=float(os.getenv("WARP_READ_TIMEOUT", "120")),
            max_connections=int(os.getenv("WARP_MAX_CONNECTIONS", "100")),
            max_keepalive=int(os.getenv("WARP_MAX_KEEPALIVE", "20")),
        )

@dataclass
class WARPHealth:
    healthy: bool = False
    last_check: float = 0.0
    warp_status: str = "unknown"
    public_ip: str = ""
    consecutive_failures: int = 0

async def check_warp_reachable(proxy_url: str, timeout: float = 5.0) -> bool:
    try:
        if not proxy_url.startswith("socks5://"):
            return False
        host_port = proxy_url[len("socks5://"):]
        host = host_port.split(":")[0]
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=timeout)
        return True
    except Exception:
        return False


class WARPProxy:

    def __init__(self, config: WARPConfig | None = None) -> None:
        self.config = config or WARPConfig.from_env()
        self.health = WARPHealth()
        self._client: httpx.AsyncClient | None = None
        self._direct_client: httpx.AsyncClient | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._warp_available: bool | None = None

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("WARP proxy disabled")
            return
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task

        self._warp_available = await check_warp_reachable(self.config.proxy_url)
        if not self._warp_available:
            logger.warning("WARP proxy hostname not reachable, will use direct connections")
            self.config.enabled = False
            return

        self._client = self._build_client()
        if self.config.health_check_interval > 0:
            self._health_task = asyncio.create_task(self._health_loop())
        logger.info("WARP proxy started: %s", self.config.proxy_url)

    def is_warp_available(self) -> bool:
        return self._warp_available if self._warp_available is not None else False

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._direct_client:
            await self._direct_client.aclose()
            self._direct_client = None
        logger.info("WARP proxy stopped")

    def _build_client(self, use_proxy: bool = True) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(
                connect=self.config.connect_timeout,
                read=self.config.read_timeout,
                write=self.config.connect_timeout,
                pool=self.config.connect_timeout,
            ),
            "limits": httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive,
                keepalive_expiry=30,
            ),
            "follow_redirects": True,
        }
        if use_proxy and self.config.enabled and self.config.proxy_url:
            kwargs["proxy"] = self.config.proxy_url
        return httpx.AsyncClient(**kwargs)

    def get_client(self, use_proxy: bool = True) -> httpx.AsyncClient:
        if use_proxy and self.is_warp_available() and self._client is not None:
            return self._client
        if not use_proxy:
            if self._direct_client is None:
                self._direct_client = self._build_client(use_proxy=False)
            return self._direct_client
        if use_proxy and not self.is_warp_available():
            if self._direct_client is None:
                self._direct_client = self._build_client(use_proxy=False)
            return self._direct_client
        self._client = self._build_client(use_proxy=use_proxy)
        return self._client

    async def check_health(self) -> bool:
        if not self.config.enabled:
            return True
        client = self._client or self._direct_client or self._build_client()
        try:
            resp = await client.get(self.config.health_check_url)
            if resp.status_code != 200:
                self.health.healthy = False
                self.health.last_check = time.time()
                self.health.consecutive_failures += 1
                logger.warning("WARP health check returned status %d", resp.status_code)
                return False
            text = resp.text
            warp_status = "off"
            public_ip = ""
            for line in text.splitlines():
                if line.startswith("warp="):
                    warp_status = line.split("=", 1)[1].strip()
                elif line.startswith("ip="):
                    public_ip = line.split("=", 1)[1].strip()
            self.health = WARPHealth(
                healthy=warp_status in ("on", "plus"),
                last_check=time.time(),
                warp_status=warp_status,
                public_ip=public_ip,
                consecutive_failures=0,
            )
            logger.debug("WARP health: %s (ip=%s)", warp_status, public_ip)
            return self.health.healthy
        except Exception as e:
            self.health.consecutive_failures += 1
            self.health.healthy = False
            self.health.last_check = time.time()
            logger.warning("WARP health check failed: %s (failures=%d)", e, self.health.consecutive_failures)
            return False

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.health_check_interval)
            await self.check_health()

    def get_status(self) -> dict[str, object]:
        return {
            "warp_enabled": self.config.enabled,
            "warp_healthy": self.health.healthy,
            "warp_status": self.health.warp_status,
            "warp_public_ip": self.health.public_ip,
            "warp_consecutive_failures": self.health.consecutive_failures,
        }
