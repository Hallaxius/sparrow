from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from sparrow.adapters.registry import AdapterRegistry
from sparrow.config.loader import load_all_providers
from sparrow.models.config import ProvidersRuntime
from sparrow.routing.engine import Route, RoutingEngine, RoutingMode

PROJECT_ROOT = Path(__file__).parent.parent


def write_uv_stub(bin_dir: Path) -> None:
    uv_stub = bin_dir / "uv"
    uv_stub.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    uv_stub.chmod(0o755)


def bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}{resolved.as_posix()[2:]}"


def run_entrypoint(
    tmp_path: Path, config_file: Path | None = None, api_key: str | None = None
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to exercise entrypoint.sh")
    app_dir = tmp_path / "app"
    bin_dir = tmp_path / "bin"
    app_dir.mkdir()
    bin_dir.mkdir()
    write_uv_stub(bin_dir)
    env = os.environ.copy()
    if api_key is None:
        env.pop("SPARROW_API_KEY", None)
    command_parts = [
        f'PATH={shlex.quote(bash_path(bin_dir))}:"$PATH"',
        f"SPARROW_APP_DIR={shlex.quote(bash_path(app_dir))}",
    ]
    if config_file is not None:
        command_parts.append(f"SPARROW_CONFIG_FILE={shlex.quote(bash_path(config_file))}")
    if api_key is not None:
        command_parts.append(f"SPARROW_API_KEY={shlex.quote(api_key)}")
    command_parts.append(shlex.quote(bash_path(PROJECT_ROOT / "entrypoint.sh")))
    return subprocess.run(
        [bash, "-lc", " ".join(command_parts)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_entrypoint_fails_when_configured_json_is_missing(tmp_path):
    config_file = tmp_path / "missing.json"

    result = run_entrypoint(tmp_path, config_file)

    assert result.returncode != 0
    assert "configuration file not found" in result.stderr
    assert "sparrow init" in result.stderr
    assert "init" not in result.stdout


def test_entrypoint_starts_server_when_configured_json_exists(tmp_path):
    providers_file = tmp_path / "providers.json"
    models_file = tmp_path / "models.json"
    providers_file.write_text('{"providers": {"p1": {"name": "P1", "base_url": "https://x.com/v1", "adapter": "openai", "auth": "none"}}, "aliases": {}}', encoding="utf-8")
    models_file.write_text('{"p1": [{"id": "m1", "name": "M1", "context": 128000, "quality": 5, "enabled": true}]}', encoding="utf-8")

    result = run_entrypoint(tmp_path, providers_file, api_key="test-key")

    assert result.returncode == 0
    assert result.stdout.splitlines()[-4:] == ["run", "python", "-m", "sparrow"]
    assert "init" not in result.stdout


def test_entrypoint_fails_when_api_key_is_missing(tmp_path):
    providers_file = tmp_path / "providers.json"
    models_file = tmp_path / "models.json"
    providers_file.write_text('{"providers": {"p1": {"name": "P1", "base_url": "https://x.com/v1", "adapter": "openai", "auth": "none"}}, "aliases": {}}', encoding="utf-8")
    models_file.write_text('{"p1": [{"id": "m1", "name": "M1", "context": 128000, "quality": 5, "enabled": true}]}', encoding="utf-8")

    result = run_entrypoint(tmp_path, providers_file)

    assert result.returncode != 0
    assert "SPARROW_API_KEY is required" in result.stderr


def _build_registry() -> tuple[AdapterRegistry, ProvidersRuntime]:
    data = load_all_providers()
    registry = AdapterRegistry()
    client = httpx.AsyncClient()
    registry.set_client(client)

    for provider_id, provider_data in data.get("providers", {}).items():
        registry.register(
            provider_id=provider_id,
            provider_name=provider_data.get("name", provider_id),
            base_url=provider_data.get("base_url", ""),
            models=provider_data.get("models", []),
        )

    return registry, data


def _build_routing_engine() -> tuple[RoutingEngine, ProvidersRuntime]:
    data = load_all_providers()
    engine = RoutingEngine()

    for provider_id, provider_data in data.get("providers", {}).items():
        for model in provider_data.get("models", []):
            if model.get("enabled", True):
                route = Route(
                    provider_id=provider_id,
                    model_id=model.get("slug", model.get("id", "")),
                    quality=model.get("quality", 5),
                    context_window=model.get("context", 128000),
                )
                engine.register_route(route)

    return engine, data


class TestIntegration:
    def test_providers_loaded(self):
        registry, _data = _build_registry()
        providers = registry.list_providers()
        assert len(providers) == 5
        providers_data = _data.get("providers", {})
        for pid in providers:
            assert pid in providers_data

    def test_adapter_types(self):
        from sparrow.adapters.openai_compat import OpenAICompatAdapter

        registry, _ = _build_registry()
        for provider_id in registry.list_providers():
            adapter = registry.get(provider_id)
            assert adapter is not None
            assert isinstance(adapter, OpenAICompatAdapter)
            assert adapter.id == provider_id

    def test_adapter_base_urls(self):
        from sparrow.adapters.openai_compat import OpenAICompatAdapter

        registry, data = _build_registry()
        for provider_id, provider_data in data.get("providers", {}).items():
            adapter = registry.get(provider_id)
            assert adapter is not None
            assert isinstance(adapter, OpenAICompatAdapter)
            expected_url = provider_data["base_url"].rstrip("/")
            assert adapter._base_url == expected_url

    def test_enabled_models_only(self):
        registry, data = _build_registry()
        for provider_id, provider_data in data.get("providers", {}).items():
            adapter = registry.get(provider_id)
            assert adapter is not None
            expected_enabled = {m["id"] for m in provider_data.get("models", []) if m.get("enabled", True)}
            assert set(adapter.available_models) == expected_enabled

    def test_routing_engine_route_count(self):
        engine, data = _build_routing_engine()
        expected_count = sum(
            1 for pd in data.get("providers", {}).values() for m in pd.get("models", []) if m.get("enabled", True)
        )
        assert engine.route_count == expected_count

    def test_routing_select_per_model(self):
        engine, data = _build_routing_engine()

        models_by_provider: dict[str, str] = {}
        for pid, pdata in data.get("providers", {}).items():
            for m in pdata.get("models", []):
                models_by_provider[m["slug"]] = pid

        target_model = "nvidia/nemotron-3-super-120b-a12b:free"
        route = engine.select(target_model, RoutingMode.MODEL)
        assert route.model_id == target_model
        assert route.provider_id == models_by_provider[target_model]

    def test_routing_select_auto_fair(self):
        engine, _ = _build_routing_engine()
        route = engine.select("auto", RoutingMode.FAIR)
        assert route.provider_id != ""
        assert route.model_id != ""

    def test_routing_fair_round_robin(self):
        engine, _ = _build_routing_engine()
        routes_seen = set()
        for _ in range(50):
            route = engine.select("auto", RoutingMode.FAIR)
            routes_seen.add(f"{route.provider_id}/{route.model_id}")
        assert len(routes_seen) > 1

    def test_routing_fast_picks_lowest_latency(self):
        engine = RoutingEngine()
        engine.register_route(Route("p1", "m1", avg_latency_ms=100))
        engine.register_route(Route("p2", "m1", avg_latency_ms=10))
        engine.register_route(Route("p3", "m1", avg_latency_ms=50))
        route = engine.select("m1", RoutingMode.FAST)
        assert route.provider_id == "p2"
        assert route.avg_latency_ms == 10

    def test_routing_quality_picks_highest(self):
        engine = RoutingEngine()
        engine.register_route(Route("p1", "m1", quality=3))
        engine.register_route(Route("p2", "m1", quality=9))
        engine.register_route(Route("p3", "m1", quality=6))
        route = engine.select("m1", RoutingMode.QUALITY)
        assert route.provider_id == "p2"
        assert route.quality == 9

    def test_all_providers_have_models(self):
        registry, _ = _build_registry()
        for provider_id in registry.list_providers():
            adapter = registry.get(provider_id)
            assert adapter is not None
            assert len(adapter.available_models) > 0, f"Provider {provider_id} has no enabled models"
