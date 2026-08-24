# Contributing to SparroW

Thanks for your interest in contributing.

---

## Getting Started

1. Fork the repository
2. Clone your fork
3. Create a branch: `git checkout -b my-feature`
4. Make your changes
5. Run tests: `uv run pytest tests/ -v`
6. Commit and push
7. Open a pull request

### Setup

```bash
git clone https://github.com/hallaxius/sparrow.git
cd sparrow
uv sync
```

Copy `.env.example` to `.env` and set `SPARROW_API_KEY`. The runtime provider source is the validated `providers.json` and `models.json`; use `sparrow init` explicitly when refreshing it.

---

## Development Workflow

### Running the proxy locally

```bash
cp .env.example .env
# Set SPARROW_API_KEY before starting the local server.
uv run python -m sparrow
```

### Running with Docker

```bash
cp .env.example .env
# Set SPARROW_API_KEY before starting Compose.
docker compose config --quiet
docker compose up -d --build
docker compose logs -f sparrow
```

### Running tests

```bash
uv run pytest tests/ -v
```

All tests must pass before submitting a PR. The suite currently contains tests.

### Linting

```bash
uv run ruff check sparrow/ tests/
uv run ruff format sparrow/ tests/
```

### Type checking

```bash
uv run mypy sparrow/
```

### Required verification order

```bash
uv run ruff check sparrow/ tests/
uv run mypy sparrow/
uv run pytest tests/ -v
uv run python -m compileall -q sparrow tests
```

### Runtime contracts

Protected API endpoints require `Authorization: Bearer YOUR-KEY`; `X-API-Key` remains available for compatibility. API keys in request bodies are rejected. `/healthz` is public liveness, while `/readyz` is public readiness and returns `503` until the process, routes, and required WARP are ready.

Routing supports `fair`, `fast`, `quality`, and `model`. The request model `auto` selects all eligible models. A request is limited to four dispatched attempts and two attempts per route; retryable upstream failures are bounded by the total deadline and `Retry-After`.

The cache is disabled by default and applies only to deterministic, non-streaming chat requests.

---

## Code Style

- **Python 3.12+** — use modern type hints (`X | None`, `list[str]`, `dict[str, Any]`)
- **No docstrings** — code should be self-documenting through clear naming
- **No comments** — unless absolutely necessary for non-obvious logic
- **Line length** — 120 characters max
- **Imports** — sorted with `ruff` (isort rules)
- **Formatter** — use `ruff format`

---

## Adding a New Provider

1. Add the provider to `providers.json` and `models.json`:

**providers.json:**
```json
{
  "providers": {
    "new-provider-uuid": {
      "name": "New Provider",
      "base_url": "https://api.newprovider.com/v1",
      "adapter": "openai",
      "auth": "none"
    }
  }
}
```

**models.json:**
```json
{
  "new-provider-uuid": [
    { "id": "model-id", "name": "Model Name", "context": 128000, "quality": 5, "enabled": true }
  ]
}
```

2. If the provider uses a non-OpenAI API, create a new adapter in `sparrow/adapters/` implementing the `ProviderAdapter` protocol from `base.py`

3. Add tests in `tests/`

---

## Adding a New Endpoint

1. Add the handler function in `sparrow/app.py`
2. Register the route in `create_app()`
3. Add tests in `tests/test_app.py`
4. Update `README.md` API reference

---

## Pull Request Checklist

Tests pass, linter passes, type checker passes, no new dependencies unless necessary, provider tested against real API before adding, README updated if adding user-facing features.

---

## Reporting Issues

Open an issue with:

- What you expected
- What actually happened
- Steps to reproduce
- Environment (OS, Python version, Docker version)
