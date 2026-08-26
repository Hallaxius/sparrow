from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_warp_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ValueError("expected one of: true, false, 1, 0, yes, no")


def _parse_routing(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"fair", "fast", "quality", "model"}:
            return normalized
    raise ValueError("expected one of: fair, fast, quality, model")


def _validate_proxy_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("must be a valid socks5 or socks5h URL with a hostname and port") from error
    if parsed.scheme.lower() not in {"socks5", "socks5h"} or not parsed.hostname or port is None:
        raise ValueError("must be a valid socks5 or socks5h URL with a hostname and port")
    return value


def _validate_health_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be a valid HTTP or HTTPS URL with a hostname")
    return value


class Settings(BaseSettings):
    host: str = Field(default="0.0.0.0", alias="SPARROW_HOST")
    port: int = Field(default=8080, alias="SPARROW_PORT")
    routing: str = Field(default="fair", alias="SPARROW_ROUTING")
    cooldown_seconds: int = Field(default=60, alias="SPARROW_COOLDOWN")

    warp_proxy_url: str = Field(
        default="socks5://warp:1080",
        validation_alias=AliasChoices("SPARROW_WARP_URL", "warp_proxy_url"),
    )
    warp_http_proxy_url: str = Field(
        default="",
        validation_alias=AliasChoices("SPARROW_WARP_HTTP_URL", "warp_http_proxy_url"),
    )
    warp_health_check_url: str = Field(
        default="https://cloudflare.com/cdn-cgi/trace",
        validation_alias=AliasChoices(
            "WARP_HEALTH_CHECK_URL",
            "SPARROW_WARP_HEALTH_CHECK_URL",
            "warp_health_check_url",
        ),
    )
    warp_health_interval: int = Field(
        default=60,
        ge=0,
        validation_alias=AliasChoices("WARP_HEALTH_INTERVAL", "SPARROW_WARP_HEALTH_INTERVAL", "warp_health_interval"),
    )
    warp_connect_timeout: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices("WARP_CONNECT_TIMEOUT", "SPARROW_WARP_CONNECT_TIMEOUT", "warp_connect_timeout"),
    )
    warp_read_timeout: float = Field(
        default=120.0,
        gt=0,
        validation_alias=AliasChoices("WARP_READ_TIMEOUT", "SPARROW_WARP_READ_TIMEOUT", "warp_read_timeout"),
    )
    warp_max_connections: int = Field(
        default=100,
        ge=1,
        validation_alias=AliasChoices("WARP_MAX_CONNECTIONS", "SPARROW_WARP_MAX_CONNECTIONS", "warp_max_connections"),
    )
    warp_max_keepalive: int = Field(
        default=20,
        ge=0,
        validation_alias=AliasChoices("WARP_MAX_KEEPALIVE", "SPARROW_WARP_MAX_KEEPALIVE", "warp_max_keepalive"),
    )

    api_key: str = Field(default="", alias="SPARROW_API_KEY")

    @field_validator("routing", mode="before")
    @classmethod
    def validate_routing(cls, value: Any) -> str:
        return _parse_routing(value)

    @field_validator("warp_proxy_url")
    @classmethod
    def validate_warp_proxy_url(cls, value: str) -> str:
        return _validate_proxy_url(value)

    @field_validator("warp_health_check_url")
    @classmethod
    def validate_warp_health_check_url(cls, value: str) -> str:
        return _validate_health_url(value)

    model_config = SettingsConfigDict(
        env_prefix="SPARROW_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )
