from __future__ import annotations

import itertools
import time
import uuid
from collections.abc import AsyncIterator

import httpx
from fake_useragent import UserAgent
from pydantic import ValidationError

from sparrow.errors import UpstreamResponseError
from sparrow.models import (
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
)
from sparrow.models.config import ProviderModelRuntime

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
        models: list[ProviderModelRuntime],
        client: httpx.AsyncClient,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
    ) -> None:
        self._id = provider_id
        self._name = provider_name
        self._base_url = base_url.rstrip("/")
        self._models = models
        self._client = client
        self._api_keys = list(api_keys or [])
        if api_key and api_key not in self._api_keys:
            self._api_keys.append(api_key)
        self._key_cycle = itertools.cycle(self._api_keys) if self._api_keys else None
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

    def rotate_key(self) -> None:
        if self._key_cycle is not None:
            next(self._key_cycle)

    def _current_key(self) -> str | None:
        if self._key_cycle is None:
            return None
        return next(self._key_cycle)

    def _build_headers(self) -> dict[str, str]:
        headers = dict(self._extra_headers)
        key = self._current_key()
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
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

    async def chat_completion(self, request: ChatRequest, model: str, **kwargs: object) -> ChatResponse:
        url = f"{self._base_url}{self._chat_path}"
        payload = request.model_dump(exclude_none=True)
        if request.extra_body:
            payload.update(request.extra_body)
        payload["model"] = model

        response = await self._client.post(url, json=payload, headers=self._build_headers())
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                self.rotate_key()
            raise
        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise UpstreamResponseError(self._id, "chat") from exc

        if not isinstance(data, dict):
            raise UpstreamResponseError(self._id, "chat")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise UpstreamResponseError(self._id, "chat")

        try:
            return ChatResponse.model_validate(
                {
                    "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "created": data.get("created") or int(time.time()),
                    "model": data.get("model") or model,
                    "choices": choices,
                    "usage": data.get("usage") or {},
                }
            )
        except ValidationError as exc:
            raise UpstreamResponseError(self._id, "chat") from exc

    async def chat_completion_stream(self, request: ChatRequest, model: str, **kwargs: object) -> AsyncIterator[str]:
        url = f"{self._base_url}{self._chat_path}"
        payload = request.model_dump(exclude_none=True)
        payload["model"] = model
        payload["stream"] = True

        last_error: httpx.HTTPStatusError | None = None
        for _ in range(max(1, len(self._api_keys))):
            async with self._client.stream("POST", url, json=payload, headers=self._build_headers()) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code == 429:
                        self.rotate_key()
                        continue
                    raise
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        if not data_lines:
                            continue
                        data_str = "\n".join(data_lines)
                        data_lines = []
                        if data_str.strip() == "[DONE]":
                            return
                        yield data_str
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:]
                        if data_str.startswith((" ", "\t")):
                            data_str = data_str[1:]
                        data_lines.append(data_str)

                if data_lines:
                    data_str = "\n".join(data_lines)
                    if data_str.strip() != "[DONE]":
                        yield data_str
                break
        if last_error is not None:
            raise last_error

    async def embedding(self, request: EmbeddingRequest, model: str, **kwargs: object) -> EmbeddingResponse:
        url = f"{self._base_url}/embeddings"
        payload: dict[str, object] = {
            "model": model,
            "input": request.input,
        }
        if request.encoding_format:
            payload["encoding_format"] = request.encoding_format

        response = await self._client.post(url, json=payload, headers=self._build_headers())
        response.raise_for_status()
        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise UpstreamResponseError(self._id, "embedding") from exc

        if not isinstance(data, dict):
            raise UpstreamResponseError(self._id, "embedding")

        embeddings = data.get("data")
        if not isinstance(embeddings, list) or not embeddings:
            raise UpstreamResponseError(self._id, "embedding")

        try:
            return EmbeddingResponse.model_validate(
                {
                    "object": data.get("object") or "list",
                    "data": embeddings,
                    "model": data.get("model") or model,
                    "usage": data.get("usage") or {},
                }
            )
        except ValidationError as exc:
            raise UpstreamResponseError(self._id, "embedding") from exc
