import pytest


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


@pytest.fixture(autouse=True)
def reset_api_key_store():
    from sparrow.middleware import auth
    auth._api_key_store = None
    yield
    auth._api_key_store = None


@pytest.fixture
async def initialized_client(app):
    from sparrow.app import lifespan

    async with lifespan(app):
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
