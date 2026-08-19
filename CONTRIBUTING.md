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

---

## Development Workflow

### Running the proxy locally

```bash
uv run python -m sparrow
```

### Running with Docker

```bash
docker compose up -d --build
docker compose logs -f sparrow
```

### Running tests

```bash
uv run pytest tests/ -v
```

All 60 tests must pass before submitting a PR.

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

## Code Style

- **Python 3.12+** — use modern type hints (`X | None`, `list[str]`, `dict[str, Any]`)
- **No docstrings** — code should be self-documenting through clear naming
- **No comments** — unless absolutely necessary for non-obvious logic
- **Line length** — 120 characters max
- **Imports** — sorted with `ruff` (isort rules)
- **Formatter** — use `ruff format`

---

## Adding a New Provider

1. Add the provider to `providers.toml`:

```toml
[providers.new-provider]
name = "New Provider"
base_url = "https://api.newprovider.com/v1"
adapter = "openai"
auth = "none"
models = [
    { id = "model-id", name = "Model Name", context = 128000, quality = 5, enabled = true },
]
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
