from __future__ import annotations

from pydantic import BaseModel


class ProviderModelConfig(BaseModel):
    id: str
    name: str
    context: int = 128000
    quality: int = 5
    enabled: bool = True


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    adapter: str = "openai"
    auth: str = "none"
    models: list[ProviderModelConfig] = []
