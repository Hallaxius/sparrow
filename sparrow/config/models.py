from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    host: str = Field(default="0.0.0.0", alias="SPARROW_HOST")
    port: int = Field(default=8080, alias="SPARROW_PORT")
    routing: str = Field(default="fair", alias="SPARROW_ROUTING")
    cooldown_seconds: int = Field(default=60, alias="SPARROW_COOLDOWN")

    warp_enabled: bool = Field(default=False, alias="SPARROW_WARP_ENABLED")
    warp_proxy_url: str = Field(default="socks5://warp:1080", alias="SPARROW_WARP_URL")

    model_config = {"env_prefix": "SPARROW_", "env_file": ".env"}
