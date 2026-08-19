# @hallaxius/sparrow

**OpenAI-compatible router for keyless free LLM providers — automatic failover, API key management, rate limiting, response caching, and IP rotation via Cloudflare WARP. No upstream API keys required.**

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
- [providers.toml](#providerstoml)
- [Architecture](#architecture)
- [Routing Modes](#routing-modes)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)

---

## Overview

SparroW aggregates multiple free LLM providers behind a single OpenAI-compatible API. Point any OpenAI SDK or client at SparroW and get automatic failover across **7 providers** and **26 models** — no API keys to the upstream providers required.

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
- ✅ **Health Check** — `/healthz` with uptime, route count, WARP status

### Routing

- ✅ **Automatic Failover** — tries next provider on timeout or HTTP error
- ✅ **Model Aliases** — request `gpt-4o`, get routed to the best free equivalent
- ✅ **Routing Modes** — `fair` (round-robin), `fast` (lowest latency), `quality` (highest score)
- ✅ **Health Tracking** — circuit breaker prevents repeated calls to failing providers
- ✅ **Daily Quotas** — per-provider daily request limits

### Security

- ✅ **API Key Management** — create, list, delete, rate-limit keys via REST
- ✅ **Rate Limiting** — per-key request limits with configurable windows
- ✅ **In-Memory Keys** — keys stored in memory, wiped on rebuild (intentional)

### Infrastructure

- ✅ **WARP Proxy** — Cloudflare WARP integration for IP rotation
- ✅ **Response Caching** — optional in-memory cache with configurable TTL
- ✅ **Request Statistics** — track provider usage, latency, success rates
- ✅ **Dashboard** — built-in HTML dashboard with live stats

---

## Providers

| Provider | Models | Quality Range |
|---|---|---|
| **OVHcloud** | Qwen 2.5 VL 72B, Mistral Small 3.2, Mistral Nemo, Qwen 3 32B, Qwen3 Coder 30B, Mistral 7B | 5–8 |
| **Kilo Gateway** | Nemotron 3 Super 120B, Nemotron 3 Ultra 550B, OpenRouter Free, Hy3 Free, Laguna S 2.1 | 6–9 |
| **OpenCode Zen** | MiMo V2.5, DeepSeek V4 Flash, Nemotron 3 Ultra, Hy3, Nemotron 3.5 Lightning | 7–8 |
| **LLM7** | Default, GPT-OSS 20B, MiniMax M2.7 | 6 |
| **BlockRun** | GPT-OSS 20B, GPT-OSS 120B, Step 3.7 Flash, Nemotron Nano 9B, Nemotron Nano 12B VL, Nemotron 3 Nano Omni 30B | 6–8 |
| **Algoholia** | Algoholia Free | 5 |

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
pip install uv
uv sync
uv run python -m sparrow
```

**Requirements:**

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker + Docker Compose (for WARP proxy)

---

## Quick Start

### 1. Start the proxy

```bash
docker compose up -d --build
```

### 2. Get your API key

```bash
docker compose logs sparrow | grep "Default API key"
```

### 3. Make a request

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-YOUR-KEY" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 4. Verify it works

```bash
curl http://localhost:8080/healthz
```

```json
{
  "status": "ok",
  "uptime_seconds": 5,
  "total_routes": 26,
  "providers": 6,
  "warp_enabled": true,
  "warp_healthy": true
}
```

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
  -H "Authorization: Bearer sk-YOUR-KEY" \
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

### API Key Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/apikeys` | List all API keys |
| POST | `/v1/apikeys` | Create a new API key |
| PATCH | `/v1/apikeys/{key_hash}` | Update an API key |
| DELETE | `/v1/apikeys/{key_hash}` | Delete an API key |

**Create key:**

```json
{
  "name": "my-key",
  "rate_limit": 100,
  "rate_window": 60
}
```

### Health & Stats

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/healthz` | Health check (no auth required) |
| GET | `/stats` | Request statistics |

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
| `SPARROW_ROUTING` | `fair` | Routing mode (`fair`, `fast`, `quality`) |
| `SPARROW_WARP_ENABLED` | `false` | Enable WARP proxy |
| `SPARROW_WARP_URL` | `socks5://warp:1080` | WARP SOCKS5 proxy URL |
| `WARP_HEALTH_INTERVAL` | `60` | WARP health check interval (seconds) |
| `WARP_CONNECT_TIMEOUT` | `10` | WARP connection timeout (seconds) |
| `WARP_READ_TIMEOUT` | `120` | WARP read timeout (seconds) |

---

## providers.toml

Providers and models are configured in `providers.toml`.

### Provider entry

```toml
[providers.my-provider]
name = "My Provider"
base_url = "https://api.example.com/v1"
adapter = "openai"
auth = "none"
models = [
    { id = "model-id", name = "Model Name", context = 128000, quality = 5, enabled = true },
]
```

### Model fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Model identifier (used in API requests) |
| `name` | string | Display name |
| `context` | int | Context window size (tokens) |
| `quality` | int | Quality score 1–10 (higher = better) |
| `enabled` | bool | Whether the model is active |

### Aliases

```toml
[aliases]
"gpt-4o" = "kilo/nvidia/nemotron-3-super-120b-a12b:free"
"auto" = "fair"
```

---

## Architecture

```
sparrow/
├── sparrow/
│   ├── app.py              # Starlette application, endpoint handlers
│   ├── client.py           # Async HTTP client with WARP support
│   ├── proxy.py            # Cloudflare WARP SOCKS5 proxy manager
│   ├── cache.py            # In-memory response cache
│   ├── stats.py            # Request statistics tracker
│   ├── dashboard.py        # HTML dashboard UI
│   ├── errors.py           # Exception hierarchy
│   ├── adapters/
│   │   ├── base.py         # ProviderAdapter protocol
│   │   ├── openai_compat.py # OpenAI-compatible adapter implementation
│   │   └── registry.py     # Adapter registry (provider_id → adapter)
│   ├── config/
│   │   ├── loader.py       # TOML config loader
│   │   ├── aliases.py      # Model alias resolver
│   │   └── models.py       # Pydantic settings model
│   ├── middleware/
│   │   ├── auth.py         # API key auth + rate limiting middleware
│   │   ├── logging.py      # Request logging
│   │   └── rate_limit.py   # IP-based rate limiter
│   ├── models/
│   │   ├── chat.py         # Chat completion request/response models
│   │   ├── embedding.py    # Embedding request/response models
│   │   ├── provider.py     # Provider/model info models
│   │   └── config.py       # Provider config models
│   └── routing/
│       ├── engine.py       # Routing engine (fair/fast/quality modes)
│       ├── health.py       # Circuit breaker + health tracking
│       ├── modes.py        # Routing strategy functions
│       └── quota.py        # Daily quota tracker
├── tests/                  # 60 tests (pytest + pytest-asyncio)
├── scripts/
│   └── init.sh             # Startup script
├── providers.toml          # Provider/model configuration
├── docker-compose.yml      # Docker Compose (sparrow + WARP)
├── Dockerfile              # Python 3.12-slim + uv
├── pyproject.toml          # Project metadata + dev tools
└── .gitignore
```

### Request Flow

```
Client → AuthMiddleware → chat_completions()
  → AliasResolver.resolve(model)
  → RoutingEngine.get_candidates(model)
  → For each candidate route:
      → AdapterRegistry.get(provider_id)
      → adapter.chat_completion() / chat_completion_stream()
      → On success: return response
      → On failure: log, try next route
  → If all fail: return 503
```

---

## Routing Modes

| Mode | Behavior |
|---|---|
| `fair` | Round-robin across all healthy routes |
| `fast` | Pick the route with lowest average latency |
| `quality` | Pick the route with highest quality score |

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

---

## Deployment

### Docker Compose

```bash
docker compose up -d --build
```

This starts both SparroW and the WARP proxy container. WARP takes ~60–90 seconds to connect on first boot.

### Local

```bash
uv run python -m sparrow
```

Note: WARP proxy is not available in local mode unless you have a SOCKS5 proxy running.

### Health Check

```bash
curl http://localhost:8080/healthz
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT
