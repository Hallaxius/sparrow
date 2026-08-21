from sparrow.config.aliases import AliasResolver
from sparrow.config.loader import load_config, load_providers_toml


def test_load_config():
    config = load_config()
    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.routing == "fair"

def test_load_providers_toml():
    data = load_providers_toml()
    assert "providers" in data
    assert "ovhcloud" in data["providers"]
    assert "kilo" in data["providers"]
    assert "blockrun" in data["providers"]

def test_alias_resolver():
    resolver = AliasResolver()
    assert resolver.resolve("gpt-4o") == "kilo/nvidia/nemotron-3-super-120b-a12b:free"
    assert resolver.resolve("unknown-model") == "unknown-model"
    assert resolver.resolve("auto") == "fair"
