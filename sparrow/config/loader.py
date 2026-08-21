from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from sparrow.config.models import Settings


def load_config() -> Settings:
    return Settings()

def load_providers_toml() -> dict[str, Any]:
    config_path = Path(__file__).parent.parent.parent / "providers.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    return {}

def load_aliases() -> dict[str, str]:
    providers_data = load_providers_toml()
    return {str(k): str(v) for k, v in providers_data.get("aliases", {}).items()}
