from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fake_useragent import UserAgent

from sparrow.models import (
    ChatChoice,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUsage,
    Usage,
)

_GPT_CHAT_HEADERS: dict[str, str] = {
    "Referer": "https://gpt.chat/",
    "Origin": "https://gpt.chat",
}

_CODEX_CHAT_HEADERS: dict[str, str] = {
    "Referer": "https://codex.chat/",
    "Origin": "https://codex.chat",
}


class OpenAICompatAdapter:

    def __init__(
        self,
        provider_id: str,
        provider_name: str,
        base_url: str,
        models: list[dict[str, Any]],
        client: httpx.AsyncClient,
    ) -> None:
        self._id = provider_id
        self._name = provider_name
        self._base_url = base_url.rstrip("/")
        self._models = models
        self._client = client
        self._ua = UserAgent()

        if "gpt.chat" in self._base_url:
            self._chat_path = "/api/chat"
            self._extra_headers = dict(_GPT_CHAT_HEADERS)
        elif "codex.chat" in self._base_url:
            self._chat_path = "/api/chat"
            self._extra_headers = dict(_CODEX_CHAT_HEADERS)
        else:
            self._chat_path = "/chat/completions"
            self._extra_headers = {}

    def _build_headers(self) -> dict[str, str]:
        headers = dict(self._extra_headers)
        headers["User-Agent"] = self._ua.random
        return headers

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def available_models(self) -> list[str]:
        return [m.get("slug", m.get("id", "")) for m in self._models if m.get("enabled", True)]

    def is_available(self) -> bool:
        return len(self.available_models) > 0

    async def chat_completion(
        self, request: ChatRequest, model: str, **kwargs: object
    ) -> ChatResponse:
        url = f"{self._base_url}{self._chat_path}"
        payload = request.model_dump(exclude_none=True)
        payload["model"] = model

        response = await self._client.post(url, json=payload, headers=self._build_headers())
        response.raise_for_status()
        data = response.json()

        return ChatResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=data.get("created", int(time.time())),
            model=data.get("model", model),
            choices=[
                ChatChoice(
                    index=c.get("index", 0),
                    message=ChatMessage(**c["message"]),
                    finish_reason=c.get("finish_reason"),
                )
                for c in data.get("choices", [])
            ],
            usage=Usage(**(data.get("usage") or {})),
        )

    async def chat_completion_stream(
        self, request: ChatRequest, model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        url = f"{self._base_url}{self._chat_path}"
        payload = request.model_dump(exclude_none=True)
        payload["model"] = model
        payload["stream"] = True

        async with self._client.stream("POST", url, json=payload, headers=self._build_headers()) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        return
                    yield data_str

    async def embedding(
        self, request: EmbeddingRequest, model: str, **kwargs: object
    ) -> EmbeddingResponse:
        url = f"{self._base_url}/embeddings"
        payload: dict[str, object] = {
            "model": model,
            "input": request.input,
        }
        if request.encoding_format:
            payload["encoding_format"] = request.encoding_format

        response = await self._client.post(url, json=payload, headers=self._build_headers())
        response.raise_for_status()
        data = response.json()

        return EmbeddingResponse(
            data=[
                EmbeddingData(
                    embedding=e["embedding"],
                    index=e.get("index", 0),
                )
                for e in data.get("data", [])
            ],
            model=data.get("model", model),
            usage=EmbeddingUsage(**(data.get("usage") or {})),
        )
