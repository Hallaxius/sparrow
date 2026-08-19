import pytest


@pytest.mark.asyncio
async def test_health_check(initialized_client):
    response = await initialized_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "warp_enabled" in data


@pytest.mark.asyncio
async def test_list_models(initialized_client):
    response = await initialized_client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_list_providers(initialized_client):
    response = await initialized_client.get("/v1/providers")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_stats(initialized_client):
    response = await initialized_client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_requests" in data
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_chat_completions_no_auth(client):
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    data = response.json()
    assert "API key" in data["error"]


@pytest.mark.asyncio
async def test_chat_completions_invalid_key(client):
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-invalid-key-123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_manage_api_keys_get(client):
    response = await client.get("/v1/apikeys")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_manage_api_keys_create(client):
    response = await client.post(
        "/v1/apikeys",
        json={"name": "test-key", "rate_limit": 50},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["key"].startswith("sk-")
    assert data["name"] == "test-key"


@pytest.mark.asyncio
async def test_chat_completions_with_valid_key(initialized_client):
    from sparrow.middleware.auth import get_api_key_store
    store = get_api_key_store()
    key = store.create_key("test-chat", rate_limit=1000)

    response = await initialized_client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code in (200, 502, 503)
