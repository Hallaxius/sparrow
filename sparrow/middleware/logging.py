from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime


def generate_request_id() -> str:
    return uuid.uuid4().hex[:12]


class StructuredLogger:
    def __init__(self, name: str = "sparrow") -> None:
        self._logger = logging.getLogger(name)

    def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        request_id: str = "",
        provider: str = "",
        model: str = "",
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "info",
            "request_id": request_id,
            "method": method,
            "path": path,
            "status": status_code,
            "duration_ms": round(duration_ms, 1),
        }
        if provider:
            entry["provider"] = provider
        if model:
            entry["model"] = model
        self._logger.info(json.dumps(entry))

    def log_error(
        self,
        message: str,
        request_id: str = "",
        method: str = "",
        path: str = "",
        provider: str = "",
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "error",
            "request_id": request_id,
            "message": message,
        }
        if method:
            entry["method"] = method
        if path:
            entry["path"] = path
        if provider:
            entry["provider"] = provider
        self._logger.error(json.dumps(entry))


RequestLogger = StructuredLogger
