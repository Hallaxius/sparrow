from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sparrow.proxy import WARPConfig, WARPHealth, WARPProxy


class TestWARPConfig:
    def test_defaults(self):
        config = WARPConfig()
        assert config.enabled is True
        assert config.proxy_url == "socks5://warp:1080"

    def test_from_env_default_enabled(self):
        with patch.dict("os.environ", {}, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is True
            assert config.proxy_url == "socks5://warp:1080"

    def test_from_env_explicit_opt_out(self):
        with patch.dict("os.environ", {"SPARROW_WARP_ENABLED": "false"}, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is False

    def test_from_env_opt_out_zero(self):
        with patch.dict("os.environ", {"SPARROW_WARP_ENABLED": "0"}, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is False

    def test_from_env_opt_out_no(self):
        with patch.dict("os.environ", {"SPARROW_WARP_ENABLED": "no"}, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is False

    def test_from_env_enabled(self):
        with patch.dict("os.environ", {
            "SPARROW_WARP_ENABLED": "true",
            "SPARROW_WARP_URL": "socks5://warp:1080",
        }, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is True
            assert config.proxy_url == "socks5://warp:1080"

    def test_from_env_enabled_auto_url(self):
        with patch.dict("os.environ", {
            "SPARROW_WARP_ENABLED": "1",
        }, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is True
            assert config.proxy_url == "socks5://warp:1080"

    def test_from_env_custom_proxy_url(self):
        with patch.dict("os.environ", {
            "SPARROW_WARP_URL": "socks5://custom:1080",
        }, clear=False):
            config = WARPConfig.from_env()
            assert config.enabled is True
            assert config.proxy_url == "socks5://custom:1080"

class TestWARPHealth:
    def test_defaults(self):
        health = WARPHealth()
        assert health.healthy is False
        assert health.warp_status == "unknown"

class TestWARPProxy:
    def test_init_disabled(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        assert proxy.config.enabled is False

    def test_get_status_disabled(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        status = proxy.get_status()
        assert status["warp_enabled"] is False

    def test_build_client_no_proxy(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        client = proxy._build_client(use_proxy=False)
        assert client is not None

    def test_build_client_with_proxy(self):
        config = WARPConfig(enabled=True, proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)
        client = proxy._build_client(use_proxy=True)
        assert client is not None

    @pytest.mark.asyncio
    async def test_start_stop_disabled(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        await proxy.start()
        assert proxy._client is None
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_check_health_disabled(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        result = await proxy.check_health()
        assert result is True

    @pytest.mark.asyncio
    async def test_check_health_failure(self):
        config = WARPConfig(enabled=True, proxy_url="socks5://invalid:9999")
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

    @pytest.mark.asyncio
    async def test_health_loop_cancellation(self):
        proxy = WARPProxy(config=WARPConfig(enabled=False))
        await proxy.start()
        await proxy.stop()

class TestWARPIntegration:

    @pytest.mark.asyncio
    async def test_health_check_parsing(self):
        config = WARPConfig(enabled=True, proxy_url="socks5://warp:1080")
        proxy = WARPProxy(config=config)

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = (
            "warp=on\n"
            "ip=203.0.113.42\n"
            "colo=JFK\n"
            "gateway=yes"
        )
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
        config = WARPConfig(enabled=True, proxy_url="socks5://warp:1080")
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
        config = WARPConfig(enabled=True, proxy_url="socks5://warp:1080")
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
