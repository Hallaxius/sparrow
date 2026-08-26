from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_dockerfile_uses_pinned_images_and_json_config():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a" in dockerfile
    assert (
        "ghcr.io/astral-sh/uv:0.5.27@sha256:5adf09a5a526f380237408032a9308000d14d5947eafa687ad6c6a2476787b4f"
        in dockerfile
    )
    assert "latest" not in dockerfile
    assert "providers.json" in dockerfile
    assert "models.json" in dockerfile
    assert "providers.toml" not in dockerfile


def test_compose_requires_json_key_warp_and_readiness_healthcheck():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'SPARROW_API_KEY: "${SPARROW_API_KEY:?SPARROW_API_KEY must be set}"' in compose
    assert 'test: ["CMD", "curl", "-fsS", "http://localhost:8080/readyz"]' in compose
    assert "condition: service_healthy" in compose
    assert (
        "dublok/cloudflare-warp:v1.0.76@sha256:2202c80915e85599b076bc9916a590a2a977a12057e2626d228c36be951436b6"
        in compose
    )
    assert "latest" not in compose


def test_env_example_declares_operational_defaults_without_secret():
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SPARROW_API_KEY=replace-with-a-long-random-secret" in env_example
