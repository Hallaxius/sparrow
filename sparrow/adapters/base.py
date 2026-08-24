from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from sparrow.models import ChatRequest, ChatResponse, EmbeddingRequest, EmbeddingResponse


@runtime_checkable
class ProviderAdapter(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    async def chat_completion(self, request: ChatRequest, model: str, **kwargs: object) -> ChatResponse: ...

    def chat_completion_stream(self, request: ChatRequest, model: str, **kwargs: object) -> AsyncIterator[str]: ...

    async def embedding(self, request: EmbeddingRequest, model: str, **kwargs: object) -> EmbeddingResponse: ...

    def is_available(self) -> bool: ...

    @property
    def available_models(self) -> list[str]: ...
