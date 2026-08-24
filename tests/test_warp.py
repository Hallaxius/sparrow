from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from sparrow.client import SparrowClient, _build_warp_client
from sparrow.config.models import Settings
from sparrow.proxy import WARPConfig, WARPHealth, WARPProxy


class TestWARPConfig:
    def test_defaults(self):
        config = WARPConfig()
        assert config.proxy_url == "socks5://warp:1080"

    def test_settings_validate_warp_url_and_limits(self, monkeypatch):
        monkeypatch.setenv("SPARROW_WARP_URL", "socks5h://custom:1081")
        monkeypatch.setenv("WARP_HEALTH_INTERVAL", "15")
        monkeypatch.setenv("WARP_CONNECT_TIMEOUT", "3.5")
        monkeypatch.setenv("WARP_READ_TIMEOUT", "12")
        monkeypatch.setenv("WARP_MAX_CONNECTIONS", "40")
        monkeypatch.setenv("WARP_MAX_KEEPALIVE", "7")

        settings = Settings()
        config = WARPConfig.from_settings(settings)

        assert config.proxy_url == "socks5h://custom:1081"
        assert config.health_check_interval == 15
        assert config.connect_timeout == 3.5
        assert config.read_timeout == 12
        assert config.max_connections == 40
        assert config.max_keepalive == 7

    @pytest.mark.parametrize(
        "environment",
        [
            {"SPARROW_WARP_URL": "https://not-a-socks-proxy"},
            {"WARP_CONNECT_TIMEOUT": "invalid"},
            {"WARP_MAX_CONNECTIONS": "0"},
        ],
    )
    def test_settings_reject_invalid_warp_values(self, monkeypatch, environment):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ValidationError):
            Settings()


class TestWARPHealth:
    def test_defaults(self):
        health = WARPHealth()
        assert health.healthy is False
        assert health.warp_status == "unknown"


class TestWARPProxy:
    def test_requires_explicit_config(self):
        with pytest.raises(TypeError):
            WARPProxy()

    def test_get_status_always_enabled(self):
        proxy = WARPProxy(config=WARPConfig())
        status = proxy.get_status()
        assert status["warp_enabled"] is True

    def test_build_client_no_proxy(self):
        proxy = WARPProxy(config=WARPConfig())
        client = proxy._build_client(use_proxy=False)
        assert client is not None

    def test_build_client_with_proxy(self):
        config = WARPConfig(proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)
        client = proxy._build_client(use_proxy=True)
        assert client is not None

    @pytest.mark.asyncio
    async def test_unavailable_warp_keeps_proxy_mode_without_direct_fallback(self):
        proxy = WARPProxy(config=WARPConfig(proxy_url="socks5://warp:1080"))
        with patch("sparrow.proxy.check_warp_reachable", new=AsyncMock(return_value=False)):
            await proxy.start()

        assert proxy.is_warp_available() is False
        assert proxy._client is not None
        assert proxy.get_client(use_proxy=True) is proxy._client
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_check_health_failure(self):
        config = WARPConfig(proxy_url="socks5://invalid:9999")
        proxy = WARPProxy(config=config)
        with patch.object(proxy, "_build_client") as mock_build:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_build.return_value = mock_client
            result = await proxy.check_health()
            assert result is False
            assert proxy.health.consecutive_failures == 1

    def test_warp_client_uses_configured_transport_without_direct_fallback(self):
        config = WARPConfig(
            proxy_url="socks5://warp:1080",
            connect_timeout=3.5,
            read_timeout=12.0,
            max_connections=40,
            max_keepalive=7,
        )
        proxy = WARPProxy(config=config)
        client = _build_warp_client(proxy)

        assert isinstance(client._transport, httpx.AsyncHTTPTransport)
        assert client.timeout.connect == 3.5
        assert client.timeout.read == 12.0
        assert client._transport._pool._max_connections == 40
        assert client._transport._pool._max_keepalive_connections == 7

        import asyncio

        asyncio.run(client.aclose())

    def test_sparrow_client_falls_back_to_direct_when_warp_unavailable(self):
        warp_proxy = WARPProxy(WARPConfig())
        client = SparrowClient(warp_proxy)
        client._direct_client = MagicMock()
        client._warp_client = MagicMock()

        with patch.object(warp_proxy, "is_warp_available", return_value=False):
            result = client.get_client(use_warp=True)

        assert result is client._direct_client

    def test_sparrow_client_returns_warp_when_available(self):
        warp_proxy = WARPProxy(WARPConfig())
        client = SparrowClient(warp_proxy)
        client._direct_client = MagicMock()
        client._warp_client = MagicMock()

        with patch.object(warp_proxy, "is_warp_available", return_value=True):
            result = client.get_client(use_warp=True)

        assert result is client._warp_client

    def test_sparrow_client_raises_when_neither_client_initialized(self):
        client = SparrowClient(WARPProxy(WARPConfig()))
        with pytest.raises(RuntimeError, match="SparrowClient not started"):
            client.get_client(use_warp=True)


class TestWARPIntegration:
    @pytest.mark.asyncio
    async def test_health_check_parsing(self):
        config = WARPConfig(proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "warp=on\nip=203.0.113.42\ncolo=JFK\ngateway=yes"
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(proxy, "_build_client", return_value=mock_client):
            result = await proxy.check_health()
            assert result is True
            assert proxy.health.warp_status == "on"
            assert proxy.health.public_ip == "203.0.113.42"

    @pytest.mark.asyncio
    async def test_health_check_warp_off(self):
        config = WARPConfig(proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "warp=off\nip=10.0.0.1"
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(proxy, "_build_client", return_value=mock_client):
            result = await proxy.check_health()
            assert result is False
            assert proxy.health.warp_status == "off"

    @pytest.mark.asyncio
    async def test_proxy_ip_rotation_simulation(self):
        config = WARPConfig(proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)

        mock_responses = []
        for ip in ["1.1.1.1", "2.2.2.2", "3.3.3.3"]:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = f"warp=on\nip={ip}"
            mock_responses.append(mock_resp)

        call_count = 0

        def make_client():
            nonlocal call_count
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_responses[call_count])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            call_count += 1
            return mock_client

        with patch.object(proxy, "_build_client", side_effect=make_client):
            results = []
            for _ in range(3):
                result = await proxy.check_health()
                results.append(result)

            assert all(results)
            assert proxy.health.public_ip == "3.3.3.3"
            assert proxy.health.warp_status == "on"
