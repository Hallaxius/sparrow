from sparrow.config.loader import load_all_providers, load_config


def test_load_config():
    config = load_config()
    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.routing == "fair"


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
    total_models = sum(
        len(p["models"]) for p in data["providers"].values()
    )
    assert total_models > 0
