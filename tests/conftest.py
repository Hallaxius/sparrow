import os
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _set_test_api_key():
    os.environ["SPARROW_API_KEY"] = "test-key"
    os.environ.setdefault("SPARROW_WARP_HEALTH_INTERVAL", "0")
    yield
    os.environ.pop("SPARROW_API_KEY", None)
    os.environ.pop("SPARROW_WARP_HEALTH_INTERVAL", None)


@pytest.fixture(autouse=True)
def _mock_warp_reachable():
    with patch("sparrow.proxy.check_warp_reachable", new=AsyncMock(return_value=True)):
        yield


@pytest.fixture(autouse=True)
def reset_api_key_auth():
    from sparrow.middleware import auth

    auth._api_key_auth = None
    yield
    auth._api_key_auth = None


@pytest.fixture
def app():
    from sparrow.app import create_app

    return create_app()


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def initialized_client(app):
    from sparrow.app import lifespan

    async with lifespan(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
