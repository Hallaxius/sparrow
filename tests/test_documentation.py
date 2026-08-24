from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_readme_matches_runtime_contracts():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for required in (
        "/readyz",
        "Authorization: Bearer",
        "X-API-Key",
        "SPARROW_CACHE_ENABLED",
    ):
        assert required in readme

    for obsolete in (
        "pip install uv",
        '"api_key": "YOUR-KEY"',
        "request body `api_key`",
        "92 tests",
    ):
        assert obsolete not in readme


def test_contributing_describes_current_verification():
    contributing = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    for required in (
        "uv run ruff check sparrow/ tests/",
        "uv run mypy sparrow/",
        "uv run pytest tests/ -v",
        "uv run python -m compileall -q sparrow tests",
        "SPARROW_API_KEY",
        "Authorization: Bearer",
    ):
        assert required in contributing

    assert "All 60 tests" not in contributing
