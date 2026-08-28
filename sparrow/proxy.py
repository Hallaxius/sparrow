from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from sparrow.config.models import Settings

logger = logging.getLogger("sparrow.proxy")


def build_warp_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


@dataclass(frozen=True, slots=True)
class WARPConfig:
    proxy_url: str = "socks5://warp:1080"
    http_proxy_url: str = ""
    health_check_url: str = "https://cloudflare.com/cdn-cgi/trace"
    health_check_interval: int = 60
    health_check_timeout: float = 5.0
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    max_connections: int = 100
    max_keepalive: int = 20

    @classmethod
    def from_settings(cls, settings: Settings) -> WARPConfig:
        return cls(
            proxy_url=settings.warp_proxy_url,
            http_proxy_url=settings.warp_http_proxy_url,
            health_check_url=settings.warp_health_check_url,
            health_check_interval=settings.warp_health_interval,
            health_check_timeout=settings.warp_health_check_timeout,
            connect_timeout=settings.warp_connect_timeout,
            read_timeout=settings.warp_read_timeout,
            max_connections=settings.warp_max_connections,
            max_keepalive=settings.warp_max_keepalive,
        )


@dataclass
class WARPHealth:
    healthy: bool = False
    last_check: float = 0.0
    warp_status: str = "unknown"
    public_ip: str = ""
    consecutive_failures: int = 0
    reason: str = "unknown"


async def check_warp_reachable(proxy_url: str, timeout: float = 5.0) -> bool:
    try:
        parsed = urlsplit(proxy_url)
        if parsed.scheme.lower() not in {"socks5", "socks5h"} or not parsed.hostname:
            return False
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.getaddrinfo(parsed.hostname, parsed.port or 1080), timeout=timeout)
        return True
    except Exception:
        return False


class WARPProxy:
    def __init__(self, config: WARPConfig) -> None:
        self.config = config
        self.health = WARPHealth()
        self._client: httpx.AsyncClient | None = None
        self._direct_client: httpx.AsyncClient | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._warp_available: bool | None = None

    def _ensure_health_task(self) -> None:
        if self._warp_available and self.config.health_check_interval > 0 and self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop())

    async def start(self, monitor_health: bool = False) -> None:
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None

        self._client = self._build_client()
        self._warp_available = False
        if monitor_health and self.config.health_check_interval > 0:
            self._health_task = asyncio.create_task(self._health_loop(initial_delay=0.0))

    def is_warp_available(self) -> bool:
        return self._warp_available if self._warp_available is not None else False

    async def wait_until_available(self, timeout: float, retry_interval: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False

            if not self.is_warp_available():
                reachable = await check_warp_reachable(self.config.proxy_url, timeout=min(remaining, 5.0))
                if not reachable:
                    await asyncio.sleep(min(retry_interval, remaining))
                    continue
                self._warp_available = True

            if await self.check_health(timeout=min(remaining, self.config.health_check_timeout)):
                logger.info("WARP proxy started: %s", self.config.proxy_url)
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(retry_interval, remaining))

    async def stop(self) -> None:
        health_task = self._health_task
        warp_client = self._client
        direct_client = self._direct_client
        self._health_task = None
        self._client = None
        self._direct_client = None

        try:
            if health_task:
                health_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await health_task
        finally:
            try:
                if warp_client:
                    await warp_client.aclose()
            finally:
                try:
                    if direct_client:
                        await direct_client.aclose()
                finally:
                    self._warp_available = None
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
        if use_proxy and self.config.proxy_url:
            kwargs["proxy"] = self.config.proxy_url
            kwargs["verify"] = build_warp_ssl_context()
        return httpx.AsyncClient(**kwargs)

    def get_client(self, use_proxy: bool = True) -> httpx.AsyncClient:
        if not use_proxy:
            if self._direct_client is None:
                self._direct_client = self._build_client(use_proxy=False)
            return self._direct_client
        if self._client is None:
            self._client = self._build_client(use_proxy=True)
        return self._client

    async def check_health(self, timeout: float | None = None) -> bool:
        temporary_client = self._client is None
        client = self._client or self._build_client()
        try:
            request_timeout = self.config.health_check_timeout if timeout is None else timeout
            resp = await client.get(self.config.health_check_url, timeout=request_timeout)
            if resp.status_code != 200:
                failures = self.health.consecutive_failures + 1
                self.health = WARPHealth(
                    healthy=False,
                    last_check=time.time(),
                    warp_status="unreachable",
                    consecutive_failures=failures,
                    reason=f"http_{resp.status_code}",
                )
                self._warp_available = False
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
                reason="" if warp_status in ("on", "plus") else "warp_off",
            )
            self._warp_available = self.health.healthy
            self._ensure_health_task()
            logger.debug("WARP health: %s (ip=%s)", warp_status, public_ip)
            return self.health.healthy
        except Exception as e:
            failures = self.health.consecutive_failures + 1
            self.health = WARPHealth(
                healthy=False,
                last_check=time.time(),
                warp_status="unreachable",
                consecutive_failures=failures,
                reason=type(e).__name__,
            )
            self._warp_available = False
            logger.warning(
                "WARP health check failed: %s (failures=%d)", type(e).__name__, self.health.consecutive_failures
            )
            return False
        finally:
            if temporary_client:
                await client.aclose()

    async def _health_loop(self, initial_delay: float | None = None) -> None:
        delay = self.config.health_check_interval if initial_delay is None else initial_delay
        if delay > 0:
            await asyncio.sleep(delay)
        while True:
            await self.check_health(timeout=self.config.health_check_timeout)
            await asyncio.sleep(self.config.health_check_interval)

    def get_status(self) -> dict[str, object]:
        return {
            "warp_enabled": True,
            "warp_available": self.is_warp_available(),
            "warp_healthy": self.health.healthy,
            "warp_status": self.health.warp_status,
            "warp_public_ip": self.health.public_ip,
            "warp_consecutive_failures": self.health.consecutive_failures,
            "warp_failure_reason": self.health.reason,
        }
