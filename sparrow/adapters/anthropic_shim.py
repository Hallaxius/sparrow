from __future__ import annotations

import json
import uuid
from typing import Any

from sparrow.models import ChatMessage, ChatRequest


def anthropic_to_chat_request(body: dict[str, Any]) -> ChatRequest:
    messages_raw: list[dict[str, Any]] = body.get("messages", [])
    system_text = body.get("system")
    model = body.get("model", "")

    openai_messages: list[dict[str, Any]] = []

    if system_text:
        system_content = system_text
        if isinstance(system_text, list):
            parts = []
            for block in system_text:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            system_content = "\n".join(parts)
        openai_messages.append({"role": "system", "content": system_content})

    for msg in messages_raw:
        role = msg.get("role", "user")
        content = msg.get("content")

        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        text_parts.append("[image]")
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[tool_use: {block.get('name', '')}]")
                    elif block.get("type") == "tool_result":
                        text_parts.append(f"[tool_result: {block.get('content', '')}]")
                elif isinstance(block, str):
                    text_parts.append(block)
            openai_messages.append({"role": role, "content": "\n".join(text_parts)})
        else:
            openai_messages.append({"role": role, "content": str(content)})

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [ChatMessage(**m) for m in openai_messages],
    }

    if body.get("max_tokens"):
        kwargs["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        kwargs["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        kwargs["top_p"] = body["top_p"]
    if body.get("stream"):
        kwargs["stream"] = True
    if body.get("stop_sequences"):
        kwargs["stop"] = body["stop_sequences"]

    return ChatRequest(**kwargs)


def chat_response_to_anthropic(response_dict: dict[str, Any]) -> dict[str, Any]:
    choices = response_dict.get("choices", [])
    content_blocks: list[dict[str, str]] = []

    for choice in choices:
        message = choice.get("message", {})
        text = message.get("content", "")
        if text:
            content_blocks.append({"type": "text", "text": text})

    usage = response_dict.get("usage", {})

    return {
        "id": response_dict.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": response_dict.get("model", ""),
        "stop_reason": _map_finish_reason(choices[0].get("finish_reason") if choices else None),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _map_finish_reason(openai_reason: str | None) -> str:
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    return mapping.get(openai_reason or "", "end_turn")


def openai_chunk_to_anthropic_sse(chunk_data: dict[str, Any]) -> list[str]:
    events: list[str] = []
    choices = chunk_data.get("choices", [])
    model = chunk_data.get("model", "")
    chunk_id = chunk_data.get("id", f"msg_{uuid.uuid4().hex[:24]}")

    if choices:
        first_choice = choices[0]
        delta = first_choice.get("delta", {})
        finish = first_choice.get("finish_reason")

        if delta.get("role") == "assistant":
            events.append(
                _sse_event(
                    {
                        "type": "message_start",
                        "message": {
                            "id": chunk_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    }
                )
            )
            events.append(
                _sse_event(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    }
                )
            )

        content_text = delta.get("content")
        if content_text:
            events.append(
                _sse_event(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": content_text},
                    }
                )
            )

        if finish:
            events.append(
                _sse_event(
                    {
                        "type": "content_block_stop",
                        "index": 0,
                    }
                )
            )
            events.append(
                _sse_event(
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": _map_finish_reason(finish),
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 0},
                    }
                )
            )
            events.append(_sse_event({"type": "message_stop"}))

    usage = chunk_data.get("usage")
    if usage and not choices:
        events.append(
            _sse_event(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {
                        "output_tokens": usage.get("completion_tokens", 0),
                    },
                }
            )
        )
        events.append(_sse_event({"type": "message_stop"}))

    return events


def _sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


def create_anthropic_error(status_code: int, message: str) -> dict[str, Any]:
    error_type = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        429: "rate_limit_error",
        500: "api_error",
        502: "upstream_error",
        503: "api_error",
    }.get(status_code, "api_error")

    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }
