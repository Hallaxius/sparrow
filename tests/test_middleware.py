from __future__ import annotations

import json
import re

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from sparrow.app import SecurityHeadersMiddleware
from sparrow.middleware.body_limit import BodySizeLimitMiddleware
from sparrow.middleware.logging import StructuredLogger, generate_request_id


async def _echo(request):
    return PlainTextResponse("ok")


async def _echo_body(request):
    body = await request.body()
    return PlainTextResponse(f"len={len(body)}")


def _make_app() -> Starlette:
    routes = [
        Route("/test", _echo),
        Route("/v1/chat/completions", _echo_body, methods=["POST"]),
        Route("/v1/embeddings", _echo_body, methods=["POST"]),
    ]
    return Starlette(routes=routes)


@pytest.mark.asyncio
async def test_body_size_limit_chat_completions():
    app = BodySizeLimitMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            content=b"x" * 1_048_576,
        )
        assert resp.status_code == 200

        resp = await client.post(
            "/v1/chat/completions",
            content=b"x" * 1_048_577,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"] == "Request body too large"
        assert body["max_bytes"] == 1_048_576


@pytest.mark.asyncio
async def test_body_size_limit_embeddings():
    app = BodySizeLimitMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/embeddings",
            content=b"x" * 511_000,
        )
        assert resp.status_code == 200

        resp = await client.post(
            "/v1/embeddings",
            content=b"x" * 512_001,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"] == "Request body too large"
        assert body["max_bytes"] == 512_000


@pytest.mark.asyncio
async def test_body_size_limit_get_passes_through():
    app = BodySizeLimitMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_security_headers_nosniff():
    app = SecurityHeadersMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_security_headers_frame_deny():
    app = SecurityHeadersMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.headers["X-Frame-Options"] == "DENY"


@pytest.mark.asyncio
async def test_security_headers_referrer_policy():
    app = SecurityHeadersMiddleware(_make_app())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_generate_request_id_is_12_char_hex():
    rid = generate_request_id()
    assert len(rid) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", rid), f"Not 12-char hex: {rid!r}"


def test_generate_request_id_unique():
    ids = {generate_request_id() for _ in range(50)}
    assert len(ids) == 50


def test_structured_logger_log_request_json(caplog):
    logger = StructuredLogger(name="test_structured_logger")

    with caplog.at_level("INFO", logger="test_structured_logger"):
        logger.log_request(
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            duration_ms=42.3,
            request_id="abc123def456",
        )

    assert len(caplog.records) == 1
    entry = json.loads(caplog.records[0].getMessage())
    assert entry["request_id"] == "abc123def456"
    assert entry["method"] == "POST"
    assert entry["path"] == "/v1/chat/completions"
    assert entry["status"] == 200
    assert entry["duration_ms"] == 42.3
    assert entry["level"] == "info"
    assert "timestamp" in entry


def test_structured_logger_log_error_includes_phase(caplog):
    logger = StructuredLogger(name="test_structured_logger")

    with caplog.at_level("ERROR", logger="test_structured_logger"):
        logger.log_error(
            message="Upstream request failed",
            request_id="abc123def456",
            provider="provider-1",
            model="model-1",
            error_type="RemoteProtocolError",
            phase="stream_after_first_chunk",
        )

    assert len(caplog.records) == 1
    entry = json.loads(caplog.records[0].getMessage())
    assert entry["phase"] == "stream_after_first_chunk"
    assert entry["error_type"] == "RemoteProtocolError"


def test_structured_logger_log_cancellation_is_neutral(caplog):
    logger = StructuredLogger(name="test_structured_logger")

    with caplog.at_level("INFO", logger="test_structured_logger"):
        logger.log_cancellation(
            request_id="abc123def456",
            method="POST",
            path="/v1/chat/completions",
            provider="provider-1",
            model="model-1",
            phase="stream_after_first_chunk",
        )

    assert len(caplog.records) == 1
    entry = json.loads(caplog.records[0].getMessage())
    assert entry["event"] == "client_cancelled"
    assert entry["outcome"] == "client_cancelled"
    assert entry["phase"] == "stream_after_first_chunk"
    assert "status" not in entry
