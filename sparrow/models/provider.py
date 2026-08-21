from __future__ import annotations

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""
    context_window: int = 0
    quality_score: int = 0
    capabilities: list[str] = []

class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo]

class ProviderInfo(BaseModel):
    id: str
    name: str
    adapter: str
    auth_type: str = "none"
    base_url: str
    models: list[str]
    available: bool

class RouteHealth(BaseModel):
    provider: str
    model: str
    healthy: bool
    latency_ms: float = 0
    success_rate: float = 1.0
    last_check: int = 0
    cooldown_until: int = 0
    requests_today: int = 0
