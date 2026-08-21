import pytest

from sparrow.middleware.auth import get_api_key_auth


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
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    data = response.json()
    assert "API key required" in data["error"]


@pytest.mark.asyncio
async def test_chat_completions_invalid_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-invalid-key-123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_body_key_required(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_body_key_accepted(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "api_key": "test-key",
        },
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_chat_completions_header_key_accepted(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_chat_completions_x_api_key_header_accepted(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_chat_completions_invalid_body_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "api_key": "wrong-key",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_embeddings_no_auth(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/embeddings",
        json={"model": "auto", "input": "hello"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_embeddings_with_valid_key(client):
    get_api_key_auth().set_keys("test-key")
    response = await client.post(
        "/v1/embeddings",
        json={"model": "auto", "input": "hello", "api_key": "test-key"},
    )
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_metrics_returns_prometheus(initialized_client):
    response = await initialized_client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contains_expected_metrics(initialized_client):
    response = await initialized_client.get("/metrics")
    body = response.text
    assert "sparrow_requests_total" in body
    assert "sparrow_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_list_providers_enrichment(initialized_client):
    response = await initialized_client.get("/v1/providers")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    for provider in data["data"]:
        assert "circuit_breaker_state" in provider
        assert "quota_used_today" in provider
        assert "avg_latency_ms" in provider
        assert "success_rate" in provider


@pytest.mark.asyncio
async def test_cors_headers_present(initialized_client):
    response = await initialized_client.get("/healthz", headers={"Origin": "http://example.com"})
    assert "access-control-allow-origin" in response.headers


@pytest.mark.asyncio
async def test_security_headers_present(initialized_client):
    response = await initialized_client.get("/healthz")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
