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


def write_init_json(
    tmp_path: Path,
    *,
    aliases: dict[str, str] | None = None,
    models: list[dict[str, object]] | None = None,
) -> None:
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
        "aliases": aliases if aliases is not None else {"default": "alpha/alpha-old"},
    }
    model_catalog = {
        "alpha": [
            {"id": "alpha-old", "name": "Alpha Old", "context": 4096, "quality": 3, "enabled": True},
        ]
        if models is None
        else models,
    }
    (tmp_path / "providers.json").write_text(json.dumps(providers), encoding="utf-8")
    (tmp_path / "models.json").write_text(json.dumps(model_catalog), encoding="utf-8")


def read_json(tmp_path: Path):
    providers = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
    models = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    for provider_id, provider_models in models.items():
        if provider_id in providers.get("providers", {}):
            providers["providers"][provider_id]["models"] = provider_models
    return providers


def test_reconcile_models_reports_added_removed_and_disabled_ids_deterministically():
    import sparrow.init as init
    from sparrow.models.config import ProviderModelConfig

    existing_models = [
        ProviderModelConfig(id="alpha-current", name="Alpha Current", context=8192, quality=7),
        ProviderModelConfig(id="alpha-removed", name="Alpha Removed", context=4096, quality=3),
        ProviderModelConfig(id="alpha-disabled", name="Alpha Disabled", context=4096, quality=3, enabled=False),
    ]

    reconciliation_function = "reconcile_models"
    reconcile_models = getattr(init, reconciliation_function)
    reconciliation = reconcile_models(existing_models, [{"id": "alpha-new"}, {"id": "alpha-current"}])

    assert reconciliation.added_ids == ("alpha-new",)
    assert reconciliation.removed_ids == ("alpha-removed",)
    assert reconciliation.disabled_ids == ("alpha-disabled",)
    assert [model.id for model in reconciliation.models] == ["alpha-current", "alpha-new"]


@pytest.mark.parametrize("fetched_models", [None, []])
def test_reconcile_models_keeps_catalog_unchanged_when_provider_fetch_is_missing_or_empty(fetched_models):
    import sparrow.init as init
    from sparrow.models.config import ProviderModelConfig

    existing_models = [ProviderModelConfig(id="alpha-old", name="Alpha Old", context=4096, quality=3)]

    reconciliation_function = "reconcile_models"
    reconcile_models = getattr(init, reconciliation_function)
    reconciliation = reconcile_models(existing_models, fetched_models)

    assert reconciliation.models == tuple(existing_models)
    assert reconciliation.added_ids == ()
    assert reconciliation.removed_ids == ()
    assert reconciliation.disabled_ids == ()


def test_reconcile_models_is_idempotent_after_a_complete_catalog_replacement():
    import sparrow.init as init
    from sparrow.models.config import ProviderModelConfig

    existing_models = [ProviderModelConfig(id="alpha-old", name="Alpha Old", context=4096, quality=3)]
    fetched_models = [{"id": "alpha-current"}, {"id": "alpha-new"}]

    reconciliation_function = "reconcile_models"
    reconcile_models = getattr(init, reconciliation_function)
    first_reconciliation = reconcile_models(existing_models, fetched_models)
    second_reconciliation = reconcile_models(first_reconciliation.models, fetched_models)

    assert second_reconciliation.models == first_reconciliation.models
    assert second_reconciliation.added_ids == ()
    assert second_reconciliation.removed_ids == ()
    assert second_reconciliation.disabled_ids == ()


async def test_run_init_replaces_complete_catalog_and_preserves_metadata(tmp_path, monkeypatch):
    write_init_json(tmp_path, aliases={})
    FakeWARPProxy.stopped = False
    fetch_models = AsyncMock(return_value=[{"id": "alpha-new"}])
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
    assert data["aliases"] == {}
    assert models == [
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


async def test_run_init_rejects_alias_targeting_removed_model_before_writing(tmp_path, monkeypatch):
    write_init_json(tmp_path)
    before_providers = (tmp_path / "providers.json").read_text(encoding="utf-8")
    before_models = (tmp_path / "models.json").read_text(encoding="utf-8")
    fetch_models = AsyncMock(return_value=[{"id": "alpha-new"}])
    monkeypatch.setattr("sparrow.init._config_path", lambda: tmp_path / "providers.json")
    monkeypatch.setattr("sparrow.init.WARPProxy", FakeWARPProxy)
    monkeypatch.setattr("sparrow.init.fetch_models_from_provider", fetch_models)

    from sparrow.init import run_init

    exit_code = await run_init()

    assert exit_code == 1
    assert (tmp_path / "providers.json").read_text(encoding="utf-8") == before_providers
    assert (tmp_path / "models.json").read_text(encoding="utf-8") == before_models


async def test_run_init_rejects_alias_targeting_disabled_model_before_writing(tmp_path, monkeypatch):
    from sparrow.config.models import Settings
    from sparrow.models.config import ProviderConfig, ProviderModelConfig, ProvidersConfig

    write_init_json(tmp_path)
    before_providers = (tmp_path / "providers.json").read_text(encoding="utf-8")
    before_models = (tmp_path / "models.json").read_text(encoding="utf-8")
    provider = ProviderConfig(
        name="Alpha",
        base_url="https://alpha.example/v1",
        adapter="openai",
        auth="none",
        daily_quota=17,
        models=[
            ProviderModelConfig(id="alpha-current", name="Alpha Current", context=4096, quality=3),
            ProviderModelConfig(id="alpha-disabled", name="Alpha Disabled", context=4096, quality=3, enabled=False),
        ],
    )
    providers_config = ProvidersConfig.model_construct(
        providers={"alpha": provider}, aliases={"default": "alpha/alpha-disabled"}
    )
    fetch_models = AsyncMock(return_value=[{"id": "alpha-current"}])
    monkeypatch.setattr(
        "sparrow.init.load_existing_config", lambda: (Settings(), tmp_path / "providers.json", providers_config)
    )
    monkeypatch.setattr("sparrow.init.WARPProxy", FakeWARPProxy)
    monkeypatch.setattr("sparrow.init.fetch_models_from_provider", fetch_models)

    from sparrow.init import run_init

    exit_code = await run_init()

    assert exit_code == 1
    assert (tmp_path / "providers.json").read_text(encoding="utf-8") == before_providers
    assert (tmp_path / "models.json").read_text(encoding="utf-8") == before_models


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


def test_config_pair_write_restores_both_files_when_second_replace_fails_after_replacement(tmp_path, monkeypatch):
    write_init_json(tmp_path, aliases={})
    providers_path = tmp_path / "providers.json"
    models_path = tmp_path / "models.json"
    before_providers = providers_path.read_text(encoding="utf-8")
    before_models = models_path.read_text(encoding="utf-8")

    from sparrow.init import write_providers_config
    from sparrow.models.config import ProviderConfig, ProviderModelConfig, ProvidersConfig

    config = ProvidersConfig(
        providers={
            "alpha": ProviderConfig(
                name="Alpha",
                base_url="https://alpha.example/v1",
                adapter="openai",
                auth="none",
                models=[ProviderModelConfig(id="alpha-new", name="Alpha New")],
            )
        },
        aliases={},
    )
    original_replace = os.replace
    models_replaced = False

    def fail_models_replace(source: Path | str, destination: Path | str) -> None:
        nonlocal models_replaced
        original_replace(source, destination)
        if Path(destination) == models_path:
            if models_replaced:
                return
            models_replaced = True
            raise PermissionError("locked")

    monkeypatch.setattr("sparrow.init.os.replace", fail_models_replace)

    with pytest.raises(PermissionError, match="locked"):
        write_providers_config(providers_path, config)

    assert providers_path.read_text(encoding="utf-8") == before_providers
    assert models_path.read_text(encoding="utf-8") == before_models


def test_cli_help_exposes_explicit_catalog_check_and_reconcile_commands():
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
        [sys.executable, "-m", "sparrow", "catalog", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert root_help.returncode == 0
    assert init_help.returncode == 0
    assert "init" not in root_help.stdout
    assert "check" in init_help.stdout
    assert "reconcile" in init_help.stdout


@pytest.mark.parametrize(
    ("command", "expected_action"),
    [("check", "check"), ("reconcile", "reconcile")],
)
def test_cli_catalog_dispatches_explicit_action(monkeypatch, command, expected_action):
    import sparrow.__main__ as cli

    actions: list[str] = []

    def record_check() -> None:
        actions.append("check")

    def record_reconciliation() -> None:
        actions.append("reconcile")

    monkeypatch.setattr(cli, "run_catalog_check", record_check, raising=False)
    monkeypatch.setattr(cli, "run_catalog_reconcile", record_reconciliation, raising=False)
    monkeypatch.setattr(sys, "argv", ["sparrow", "catalog", command])

    cli.main()

    assert actions == [expected_action]


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


def test_run_server_does_not_refresh_catalog_before_starting_uvicorn(monkeypatch):
    import sparrow.__main__ as cli
    from sparrow import init

    uvicorn_called = False

    def fail_if_catalog_refresh_runs():
        raise AssertionError("catalog refresh must remain explicit")

    def record_uvicorn_call(*args, **kwargs):
        nonlocal uvicorn_called
        uvicorn_called = True

    monkeypatch.setattr(init, "run_init", fail_if_catalog_refresh_runs)
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(host="127.0.0.1", port=8080))
    monkeypatch.setattr(cli, "load_all_providers", lambda: {"providers": {}, "aliases": {}, "model_groups": {}})
    monkeypatch.setattr(cli.uvicorn, "run", record_uvicorn_call)

    cli.run_server()

    assert uvicorn_called is True
