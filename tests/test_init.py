import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class FakeWARPProxy:
    stopped = False
    config = None

    def __init__(self, config=None):
        type(self).config = config

    async def start(self):
        return None

    async def stop(self):
        type(self).stopped = True

    def is_warp_available(self):
        return False

    def get_client(self, use_proxy=True):
        return object()


def write_init_json(tmp_path: Path) -> None:
    providers = {
        "providers": {
            "alpha": {
                "name": "Alpha",
                "base_url": "https://alpha.example/v1",
                "adapter": "openai",
                "auth": "none",
                "daily_quota": 17,
            },
        },
        "aliases": {"default": "alpha/alpha-old"},
    }
    models = {
        "alpha": [
            {"id": "alpha-old", "name": "Alpha Old", "context": 4096, "quality": 3, "enabled": True},
        ],
    }
    (tmp_path / "providers.json").write_text(json.dumps(providers), encoding="utf-8")
    (tmp_path / "models.json").write_text(json.dumps(models), encoding="utf-8")


def read_json(tmp_path: Path):
    providers = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
    models = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    for provider_id, provider_models in models.items():
        if provider_id in providers.get("providers", {}):
            providers["providers"][provider_id]["models"] = provider_models
    return providers


async def test_run_init_updates_configured_json_and_preserves_metadata(tmp_path, monkeypatch):
    write_init_json(tmp_path)
    FakeWARPProxy.stopped = False
    fetch_models = AsyncMock(return_value=[{"id": "alpha-old"}, {"id": "alpha-new"}])
    monkeypatch.setattr("sparrow.init._config_path", lambda: tmp_path / "providers.json")
    monkeypatch.setattr("sparrow.init.WARPProxy", FakeWARPProxy)
    monkeypatch.setattr("sparrow.init.fetch_models_from_provider", fetch_models)

    from sparrow.init import run_init

    exit_code = await run_init()
    data = read_json(tmp_path)
    models = data["providers"]["alpha"]["models"]

    assert exit_code == 0
    assert FakeWARPProxy.stopped is True
    assert data["providers"]["alpha"]["daily_quota"] == 17
    assert data["aliases"] == {"default": "alpha/alpha-old"}
    assert models == [
        {"id": "alpha-old", "name": "Alpha Old", "context": 4096, "quality": 3, "enabled": True},
        {"id": "alpha-new", "name": "alpha-new", "context": 128000, "quality": 5, "enabled": True},
    ]


async def test_run_init_preserves_provider_models_when_refresh_returns_no_models(tmp_path, monkeypatch):
    write_init_json(tmp_path)
    fetch_models = AsyncMock(return_value=[])
    monkeypatch.setattr("sparrow.init._config_path", lambda: tmp_path / "providers.json")
    monkeypatch.setattr("sparrow.init.WARPProxy", FakeWARPProxy)
    monkeypatch.setattr("sparrow.init.fetch_models_from_provider", fetch_models)

    from sparrow.init import run_init

    exit_code = await run_init()
    data = read_json(tmp_path)

    assert exit_code == 0
    assert data["providers"]["alpha"]["models"] == [
        {"id": "alpha-old", "name": "Alpha Old", "context": 4096, "quality": 3, "enabled": True}
    ]
    assert data["aliases"] == {"default": "alpha/alpha-old"}


async def test_run_init_stops_warp_when_refresh_raises(tmp_path, monkeypatch):
    write_init_json(tmp_path)
    FakeWARPProxy.stopped = False
    fetch_models = AsyncMock(side_effect=RuntimeError("provider exploded"))
    monkeypatch.setattr("sparrow.init._config_path", lambda: tmp_path / "providers.json")
    monkeypatch.setattr("sparrow.init.WARPProxy", FakeWARPProxy)
    monkeypatch.setattr("sparrow.init.fetch_models_from_provider", fetch_models)

    from sparrow.init import run_init

    with pytest.raises(RuntimeError, match="provider exploded"):
        await run_init()

    assert FakeWARPProxy.stopped is True


def test_atomic_text_write_preserves_existing_json_when_replace_fails(tmp_path, monkeypatch):
    write_init_json(tmp_path)
    config_path = tmp_path / "providers.json"
    before = config_path.read_text(encoding="utf-8")

    def fail_replace(source: Path | str, destination: Path | str) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("sparrow.init.os.replace", fail_replace)

    from sparrow.init import write_text_atomic

    with pytest.raises(PermissionError, match="locked"):
        write_text_atomic(config_path, '{"broken": true}')

    assert config_path.read_text(encoding="utf-8") == before


def test_cli_help_mentions_json_readiness_and_explicit_init():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    root_help = subprocess.run(
        [sys.executable, "-m", "sparrow", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    init_help = subprocess.run(
        [sys.executable, "-m", "sparrow", "init", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    help_text = f"{root_help.stdout}\n{init_help.stdout}"

    assert root_help.returncode == 0
    assert init_help.returncode == 0
    assert "JSON" in help_text
    assert "readiness" in help_text.lower()
    assert "explicit" in help_text.lower()
    assert "TOML" not in help_text


def test_run_server_validates_json_before_starting_uvicorn(monkeypatch):
    from sparrow.__main__ import run_server
    from sparrow.errors import ConfigurationFileError

    uvicorn_called = False

    def fail_validation():
        raise ConfigurationFileError("providers.json", "invalid JSON")

    def record_uvicorn_call(*args, **kwargs):
        nonlocal uvicorn_called
        uvicorn_called = True

    monkeypatch.setattr("sparrow.__main__.load_config", lambda: SimpleNamespace(host="127.0.0.1", port=8080))
    monkeypatch.setattr("sparrow.__main__.load_all_providers", fail_validation)
    monkeypatch.setattr("sparrow.__main__.uvicorn.run", record_uvicorn_call)

    with pytest.raises(ConfigurationFileError, match="invalid JSON"):
        run_server()

    assert uvicorn_called is False
