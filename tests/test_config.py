import json

import pytest
from pydantic import ValidationError

from sparrow.config.loader import load_all_providers, load_config
from sparrow.config.models import Settings
from sparrow.errors import ConfigurationFileError
from sparrow.routing.engine import Route, RoutingEngine


def write_config(tmp_path, providers, aliases=None, models=None, model_groups=None):
    providers_file = tmp_path / "providers.json"
    providers_data = {"providers": providers, "aliases": aliases or {}}
    if model_groups is not None:
        providers_data["model_groups"] = model_groups
    providers_file.write_text(json.dumps(providers_data), encoding="utf-8")

    if models is None:
        models = {}
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(models), encoding="utf-8")


def test_load_all_providers_uses_env_json_with_aliases_and_quota(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "https://alpha.example/v1",
            "adapter": "openai",
            "auth": "none",
            "daily_quota": 11,
        },
        "beta": {
            "name": "Beta",
            "base_url": "https://beta.example/v1",
            "adapter": "openai",
            "auth": "none",
            "daily_quota": 22,
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": True},
            {"id": "alpha-disabled", "name": "Alpha Disabled", "context": 4096, "quality": 3, "enabled": False},
        ],
        "beta": [
            {"id": "beta-accurate", "name": "Beta Accurate", "context": 16384, "quality": 9, "enabled": True},
        ],
    }
    aliases = {"best-free": "beta/beta-accurate"}
    write_config(tmp_path, providers, aliases, models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    data = load_all_providers()
    provider_id, _separator, model_id = data["aliases"]["best-free"].partition("/")
    engine = RoutingEngine()
    for current_provider_id, provider in data["providers"].items():
        for model in provider["models"]:
            if model["enabled"]:
                engine.register_route(
                    Route(
                        current_provider_id,
                        model["slug"],
                        quality=model["quality"],
                        context_window=model["context"],
                    )
                )
    candidates = engine.get_candidates(model_id)

    assert sorted(data["providers"]) == ["alpha", "beta"]
    assert data["aliases"] == {"best-free": "beta/beta-accurate"}
    assert data["providers"]["alpha"]["daily_quota"] == 11
    assert data["providers"]["beta"]["daily_quota"] == 22
    assert data["providers"]["alpha"]["models"] == [
        {
            "id": "alpha-fast",
            "slug": "alpha-fast",
            "name": "Alpha Fast",
            "quality": 7,
            "context": 8192,
            "enabled": True,
        },
        {
            "id": "alpha-disabled",
            "slug": "alpha-disabled",
            "name": "Alpha Disabled",
            "quality": 3,
            "context": 4096,
            "enabled": False,
        },
    ]
    assert [(route.provider_id, route.model_id) for route in candidates] == [(provider_id, model_id)]


@pytest.mark.parametrize("routing", ["auto", "weighted", "", "FAIRISH"])
def test_settings_reject_invalid_routing_mode(routing):
    with pytest.raises(ValidationError, match="fair, fast, quality, model"):
        Settings(routing=routing)


def test_settings_disable_cache_by_default():
    assert Settings().cache_enabled is False


def test_load_all_providers_rejects_missing_env_config(tmp_path, monkeypatch):
    missing_path = tmp_path / "missing"
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(missing_path))

    with pytest.raises(ConfigurationFileError, match="file does not exist"):
        load_all_providers()


def test_load_all_providers_rejects_invalid_json(tmp_path, monkeypatch):
    config_path = tmp_path / "providers.json"
    config_path.write_text("{invalid json", encoding="utf-8")
    models_path = tmp_path / "models.json"
    models_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(config_path))

    with pytest.raises(ConfigurationFileError, match="invalid JSON"):
        load_all_providers()


def test_load_all_providers_rejects_invalid_alias_target(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "https://alpha.example/v1",
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": True},
        ],
    }
    aliases = {"best-free": "missing/alpha-fast"}
    write_config(tmp_path, providers, aliases, models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="provider/model"):
        load_all_providers()


def test_load_all_providers_rejects_invalid_url(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "ftp://alpha.example/v1",
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": True},
        ],
    }
    write_config(tmp_path, providers, models=models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="base_url"):
        load_all_providers()


def test_load_all_providers_rejects_provider_without_enabled_model(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "https://alpha.example/v1",
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": False},
        ],
    }
    write_config(tmp_path, providers, models=models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="enabled model"):
        load_all_providers()


def test_load_all_providers_rejects_unknown_alias_model(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "https://alpha.example/v1",
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": True},
        ],
    }
    aliases = {"best-free": "alpha/beta-fast"}
    write_config(tmp_path, providers, aliases, models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="unknown model"):
        load_all_providers()


def test_load_all_providers_rejects_malformed_alias(tmp_path, monkeypatch):
    providers = {
        "alpha": {
            "name": "Alpha",
            "base_url": "https://alpha.example/v1",
        },
    }
    models = {
        "alpha": [
            {"id": "alpha-fast", "name": "Alpha Fast", "context": 8192, "quality": 7, "enabled": True},
        ],
    }
    aliases = {"best-free": "alpha-fast"}
    write_config(tmp_path, providers, aliases, models)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="provider/model"):
        load_all_providers()


def test_load_all_providers_returns_model_groups(tmp_path, monkeypatch):
    providers = {
        "zen": {"name": "Zen", "base_url": "https://zen.example/v1"},
        "kilo": {"name": "Kilo", "base_url": "https://kilo.example/v1"},
    }
    models = {
        "zen": [{"id": "hy3-free", "name": "HY3", "enabled": True}],
        "kilo": [{"id": "tencent/hy3:free", "name": "HY3", "enabled": True}],
    }
    model_groups = {"hy3": ["hy3-free", "tencent/hy3:free"]}
    write_config(tmp_path, providers, models=models, model_groups=model_groups)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    data = load_all_providers()

    assert data["model_groups"] == {"hy3": ["hy3-free", "tencent/hy3:free"]}


def test_load_all_providers_rejects_group_with_unknown_model(tmp_path, monkeypatch):
    providers = {
        "alpha": {"name": "Alpha", "base_url": "https://alpha.example/v1"},
    }
    models = {
        "alpha": [{"id": "alpha-fast", "name": "Alpha Fast", "enabled": True}],
    }
    model_groups = {"fast": ["alpha-fast", "ghost-model"]}
    write_config(tmp_path, providers, models=models, model_groups=model_groups)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="unknown models"):
        load_all_providers()


def test_load_all_providers_rejects_single_member_group(tmp_path, monkeypatch):
    providers = {
        "alpha": {"name": "Alpha", "base_url": "https://alpha.example/v1"},
    }
    models = {
        "alpha": [{"id": "alpha-fast", "name": "Alpha Fast", "enabled": True}],
    }
    model_groups = {"fast": ["alpha-fast"]}
    write_config(tmp_path, providers, models=models, model_groups=model_groups)
    monkeypatch.setenv("SPARROW_CONFIG_FILE", str(tmp_path / "providers.json"))

    with pytest.raises(ConfigurationFileError, match="at least two"):
        load_all_providers()


def test_load_all_providers_preserves_current_json_inventory():
    data = load_all_providers()

    providers = data["providers"]

    assert sorted(providers) == [
        "1321946a-0d1a-4c00-882e-c626e19047e5",
        "17b72315-1e87-4a43-b86d-e455bfe57051",
        "a16ce1ab-4e9d-446e-85ec-34974be6091a",
        "c193adf9-0783-40fa-a892-3ad8463a2fb6",
        "c1d70340-1800-4faf-aecc-63480c0ef315",
        "e142a874-b2b2-4b25-86b3-07834bee7126",
        "f3100559-d247-449a-baa1-5092dc4fcf6c",
    ]
    assert {provider_id: len(provider["models"]) for provider_id, provider in providers.items()} == {
        "1321946a-0d1a-4c00-882e-c626e19047e5": 5,
        "17b72315-1e87-4a43-b86d-e455bfe57051": 4,
        "a16ce1ab-4e9d-446e-85ec-34974be6091a": 16,
        "c193adf9-0783-40fa-a892-3ad8463a2fb6": 13,
        "c1d70340-1800-4faf-aecc-63480c0ef315": 1,
        "e142a874-b2b2-4b25-86b3-07834bee7126": 11,
        "f3100559-d247-449a-baa1-5092dc4fcf6c": 6,
    }
    assert providers["c1d70340-1800-4faf-aecc-63480c0ef315"]["base_url"] == "https://free.empero.org/v1"
    assert providers["c1d70340-1800-4faf-aecc-63480c0ef315"]["api_keys"] == ["free"]
    assert providers["e142a874-b2b2-4b25-86b3-07834bee7126"]["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert providers["e142a874-b2b2-4b25-86b3-07834bee7126"]["api_keys"] == ["replace-with-key"]
    assert providers["a16ce1ab-4e9d-446e-85ec-34974be6091a"]["base_url"] == "https://api.kilo.ai/api/openrouter/"
    assert providers["a16ce1ab-4e9d-446e-85ec-34974be6091a"]["models"][0] == {
        "id": "tencent/hy3:free",
        "slug": "tencent/hy3:free",
        "name": "HY3",
        "quality": 5,
        "context": 128000,
        "enabled": True,
    }


def test_load_config():
    config = load_config()
    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.routing == "fair"
    assert config.cache_enabled is False


def test_load_all_providers():
    data = load_all_providers()
    assert "providers" in data
    providers = data["providers"]
    assert len(providers) > 0
    for _provider_id, provider_data in providers.items():
        assert "name" in provider_data
        assert "base_url" in provider_data
        assert "models" in provider_data
        assert isinstance(provider_data["models"], list)


def test_load_all_providers_has_models():
    data = load_all_providers()
    total_models = sum(len(p["models"]) for p in data["providers"].values())
    assert total_models > 0
