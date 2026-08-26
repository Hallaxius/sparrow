# @hallaxius/sparrow

**OpenAI-compatible router for keyless free LLM providers — automatic failover, API key management, and IP rotation via Cloudflare WARP. No upstream API keys required.**

<p align="center">
  <a href="https://github.com/hallaxius/sparrow"><img src="https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/hallaxius/sparrow"><img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker"></a>
  <a href="https://github.com/hallaxius/sparrow/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT"></a>
</p>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Providers](#providers)
- [Model Aliases](#model-aliases)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [providers.json + models.json](#providersjson--modelsjson)
- [Architecture](#architecture)
- [Routing Modes](#routing-modes)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)

---

## Overview

SparroW aggregates multiple free LLM providers behind a single OpenAI-compatible API. Point any OpenAI SDK or client at SparroW and get automatic failover across **7 providers** configured in `providers.json` and `models.json` — no API keys to the upstream providers required.

**Key Philosophy:**

- ✅ **Zero upstream keys** — all providers are free and keyless
- ✅ **OpenAI-compatible** — drop-in replacement for any OpenAI SDK or client
- ✅ **Automatic failover** — if one provider fails, the next is tried seamlessly
- ✅ **Streaming support** — full SSE streaming with failover
- ✅ **Docker-ready** — single `docker compose up` to run
- ✅ **Lightweight** — pure Python, no heavy dependencies

---

## Features

### Core

- ✅ **Chat Completions** — `/v1/chat/completions` with streaming and non-streaming
- ✅ **Embeddings** — `/v1/embeddings` endpoint
- ✅ **Model Listing** — `/v1/models` returns all available models
- ✅ **Provider Listing** — `/v1/providers` with health status
- ✅ **Health Check** — `/healthz` liveness and `/readyz` readiness with route and WARP status

### Routing

- ✅ **Automatic Failover** — tries next provider on timeout or HTTP error
- ✅ **Model Aliases** — request `gpt-4o`, get routed to the best free equivalent
- ✅ **Routing Modes** — `fair`, `fast`, `quality`, and `model` selection
- ✅ **Health Tracking** — circuit breaker prevents repeated calls to failing providers
- ✅ **Daily Quotas** — per-provider daily request limits

### Security

- ✅ **API Key Auth** — `.env`-backed static API keys, accepted through `Authorization: Bearer` or `X-API-Key`

### Infrastructure

- ✅ **WARP Proxy** — Cloudflare WARP integration for IP rotation
- ✅ **Request Statistics** — track provider usage, latency, success rates
- ✅ **Dashboard** — built-in HTML dashboard with live stats

---

## Providers

| Provider | Quality Range | Notes |
|---|---|---|
| **GPT.chat** | 5 | |
| **OpenCode Zen** | 5 | |
| **BlockRun** | 5 | |
| **Kilo Gateway** | 5 | |
| **OVH Cloud** | 5 | |
| **Codex.chat** | 5 | |
| **LLM7.io** | 5 | |

---

## Model Aliases

Request well-known model names and SparroW routes them to the best free equivalent:

| Alias | Routes to |
|---|---|
| `gpt-4o` | Kilo / Nemotron 3 Super 120B |
| `gpt-4o-mini` | Kilo / OpenRouter Free |
| `claude-3.5-sonnet` | Kilo / Nemotron 3 Ultra 550B |
| `claude-3-haiku` | OpenCode / MiMo V2.5 |
| `deepseek-r1` | OpenCode / DeepSeek V4 Flash |
| `gemini-2.5-flash` | OpenCode / DeepSeek V4 Flash |
| `mistral-small` | OVHcloud / Mistral Small 3.2 |
| `auto` | Round-robin across all providers |

---

## Installation

### Docker (recommended)

```bash
git clone https://github.com/hallaxius/sparrow.git
cd sparrow
docker compose up -d --build
```

### Local development

```bash
uv sync
cp .env.example .env
# Set SPARROW_API_KEY in .env before starting the server.
uv run python -m sparrow
```

**Requirements:**

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker + Docker Compose (for WARP proxy)

---

## Quick Start

### 1. Prepare configuration and credentials

```bash
cp .env.example .env
# Set SPARROW_API_KEY to a long random secret.
```

Sparrow reads `providers.json` and `models.json` from the repository root. `sparrow init` is an explicit refresh command and is never run automatically by the server or entrypoint.

### 2. Verify provider configuration

```bash
uv run python -c "from sparrow.config.loader import load_all_providers; print(len(load_all_providers()['providers']))"
```

### 3. Start the proxy and router

```bash
docker compose up -d --build
```

### 4. Make an authenticated request

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR-KEY" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 5. Verify liveness and readiness

```bash
curl -i http://localhost:8080/healthz
curl -i http://localhost:8080/readyz
```

`/healthz` is a public liveness endpoint and remains `200` while the process is running. `/readyz` is a public readiness endpoint: it returns `503` before startup completes, when no route is eligible, or when required WARP is unavailable; it returns `200` only when local components and at least one route are ready.

---

## API Reference

All endpoints follow the OpenAI API format.

### Chat Completions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/chat/completions` | Chat completions (streaming + non-streaming) |

**Request:**

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": false
}
```

**Streaming:**

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR-KEY" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Write a haiku"}],
    "stream": true
  }'
```

### Embeddings

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/embeddings` | Create embeddings |

```json
{
  "model": "auto",
  "input": "The quick brown fox"
}
```

### Models

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/models` | List all available models |

### Providers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/providers` | List all providers with health status |

### API Key Authentication

API keys are configured via the required `SPARROW_API_KEY` environment variable (see Configuration). Send the key in the `Authorization: Bearer YOUR-KEY` header. `X-API-Key: YOUR-KEY` is supported for compatibility. API keys in JSON request bodies are not accepted.

`/`, `/healthz`, and `/readyz` are public. Chat, embeddings, model/provider inventory, statistics, and metrics endpoints require authentication. The dashboard shell is public, but its data requests send the configured authorization header.

### Health & Stats

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/healthz` | Liveness check (no auth required) |
| GET | `/readyz` | Readiness check (no auth required; `503` when unavailable) |
| GET | `/stats` | Request statistics (auth required) |
| GET | `/metrics` | Prometheus metrics (auth required) |

### Response Headers

Every response includes provider metadata:

| Header | Description |
|--------|-------------|
| `X-Sparrow-Provider` | Provider that served the request |
| `X-Sparrow-Model` | Model that was used |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SPARROW_HOST` | `0.0.0.0` | Bind address |
| `SPARROW_PORT` | `8080` | Listen port |
| `SPARROW_ROUTING` | `fair` | Routing mode (`fair`, `fast`, `quality`, `model`) |
| `SPARROW_API_KEY` | *(required)* | Single API key accepted through `Authorization: Bearer` or `X-API-Key` |
| `SPARROW_WARP_URL` | `socks5://warp:1080` | WARP SOCKS5 proxy URL |
| `SPARROW_WARP_HTTP_URL` | *(empty)* | Optional HTTP proxy URL for WARP health traffic |
| `SPARROW_WARP_HEALTH_CHECK_URL` | `https://cloudflare.com/cdn-cgi/trace` | WARP health endpoint |
| `WARP_HEALTH_INTERVAL` | `60` | WARP health check interval (seconds) |
| `WARP_CONNECT_TIMEOUT` | `10` | WARP connection timeout (seconds) |
| `WARP_READ_TIMEOUT` | `120` | WARP read timeout (seconds) |
| `WARP_MAX_CONNECTIONS` | `100` | Maximum WARP connections |
| `WARP_MAX_KEEPALIVE` | `20` | Maximum WARP keepalive connections |
Boolean settings accept `true`, `false`, `1`, `0`, `yes`, and `no`.

---

## providers.json + models.json

Providers and models are configured in two JSON files. `providers.json` contains provider metadata and aliases, while `models.json` contains model definitions grouped by provider UUID. Sparrow reads these as the runtime provider/model source.

### Provider entry (providers.json)

```json
{
  "providers": {
    "my-provider-uuid": {
      "name": "My Provider",
      "base_url": "https://api.example.com/v1",
      "adapter": "openai",
      "auth": "none"
    }
  },
  "aliases": {
    "gpt-4o": "my-provider-uuid/model-id"
  }
}
```

### Model entry (models.json)

```json
{
  "my-provider-uuid": [
    {
      "id": "model-id",
      "name": "Model Name",
      "context": 128000,
      "quality": 5,
      "enabled": true
    }
  ]
}
```

### Model fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Model identifier (used in API requests) |
| `name` | string | Display name |
| `context` | int | Context window size (tokens) |
| `quality` | int | Quality score 1–10 (higher = better) |
| `enabled` | bool | Whether the model is active |

`daily_quota` is an optional provider-level daily request limit. It is enforced atomically for every dispatched upstream attempt; `None` means unlimited.

### Aliases

Aliases are defined in `providers.json` under the `"aliases"` key. The format is `"alias_name": "provider_uuid/model_id"`.

---

## Architecture

```
sparrow/
├── sparrow/
│   ├── app.py              # Starlette application, endpoint handlers
│   ├── client.py           # Async HTTP client with WARP support
│   ├── proxy.py            # Cloudflare WARP SOCKS5 proxy manager
│   ├── stats.py            # Request statistics tracker
│   ├── dashboard.py        # HTML dashboard UI
│   ├── errors.py           # Exception hierarchy
│   ├── adapters/
│   │   ├── base.py         # ProviderAdapter protocol
│   │   ├── openai_compat.py # OpenAI-compatible adapter implementation
│   │   └── registry.py     # Adapter registry (provider_id → adapter)
│   ├── config/
│   │   ├── loader.py       # JSON config loader
│   │   ├── aliases.py      # Model alias resolver
│   │   └── models.py       # Pydantic settings model
│   ├── middleware/
│   │   ├── auth.py         # API key auth middleware
│   │   ├── logging.py      # Request logging
│   │   └── body_limit.py   # Body size limiter
│   ├── models/
│   │   ├── chat.py         # Chat completion request/response models
│   │   ├── embedding.py    # Embedding request/response models
│   │   ├── provider.py     # Provider/model info models
│   │   └── config.py       # Provider config models
│   └── routing/
│       ├── engine.py       # Routing engine (fair/fast/quality/model modes)
│       ├── health.py       # Circuit breaker + health tracking
│       ├── modes.py        # Routing strategy functions
│       └── quota.py        # Daily quota tracker
├── tests/                  # Tests (pytest + pytest-asyncio)
├── entrypoint.sh           # Explicit JSON/API-key startup checks
├── providers.json          # Provider metadata + aliases configuration
├── models.json             # Model definitions per provider
├── docker-compose.yml      # Docker Compose (sparrow + WARP)
├── Dockerfile              # Python 3.12-slim + uv
├── pyproject.toml          # Project metadata + dev tools
└── .gitignore
```

### Request Flow

```
Client → AuthMiddleware → chat_completions()
  → Validate request and reject malformed input with safe 400
  → AliasResolver.resolve(model)
  → RoutingEngine.ordered_candidates(model)
  → For each candidate attempt within the global deadline:
      → QuotaTracker.try_acquire() and CircuitBreaker
      → AdapterRegistry.get(provider_id)
      → adapter.chat_completion() / chat_completion_stream()
      → On success: return response
      → On retryable failure: bounded retry or next route
      → On non-retryable upstream failure: next route
  → If all fail: return 503
```

---

## Routing Modes

| Mode | Behavior |
|---|---|
| `fair` | Round-robin across eligible routes for the requested model |
| `fast` | Pick the route with lowest average latency |
| `quality` | Pick the route with highest quality score |
| `model` | Preserve the configured candidate order for an explicit model |

The request model `auto` means all eligible models. The request model `fair` is an ordinary model identifier; it is not an alias for `auto`.

### Retry, quota, and SSE contracts

Each request has at most four dispatched attempts and at most two attempts on one route. HTTP `408`, `429`, `5xx`, transport errors, and timeouts are retryable; `Retry-After` is honored only within the total request deadline. Other upstream `4xx` responses advance to the next route without repeating the same route. Local validation errors are never retried. Timeout exhaustion returns `504`; other provider exhaustion returns `503`.

Streaming may retry or fail over before the first event. After the first event, the active route is retained: a failure emits one `upstream_error` event, closes the stream, and never emits a later `DONE` or switches providers.

Daily quota acquisition, request statistics, and breaker state are updated for every dispatched attempt. Circuit breakers allow exactly one half-open probe after recovery.

---

## Testing

```bash
uv run pytest tests/ -v
```

### Linting

```bash
uv run ruff check sparrow/ tests/
uv run ruff format sparrow/ tests/
```

### Type checking

```bash
uv run mypy sparrow/
```

### Full verification

```bash
uv run ruff check sparrow/ tests/
uv run mypy sparrow/
uv run pytest tests/ -v
uv run python -m compileall -q sparrow tests
```

---

## Deployment

### Docker Compose

```bash
cp .env.example .env
# Set SPARROW_API_KEY before starting Compose.
docker compose config --quiet
docker compose up -d --build
```

This starts both SparroW and the WARP proxy container. Compose enables WARP explicitly and waits for the WARP service healthcheck. WARP takes ~60–90 seconds to connect on first boot; SparroW remains not ready while required WARP is unavailable.

### Local

```bash
cp .env.example .env
# Set SPARROW_API_KEY before starting.
uv run python -m sparrow
```

Local mode uses direct HTTP when WARP is unreachable. When a configured SOCKS5/SOCKS5H proxy is available, WARP is used automatically.

### Health Check

```bash
curl http://localhost:8080/healthz
curl -i http://localhost:8080/readyz
```

`/healthz` is liveness and does not require authentication. `/readyz` is readiness and returns `503` until startup, routes, and required WARP are ready.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT
