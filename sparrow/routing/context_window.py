from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger("sparrow.routing.context_window")

CONTEXT_OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"context.?length.?exceed", re.IGNORECASE),
    re.compile(r"too.?many.?tokens", re.IGNORECASE),
    re.compile(r"maximum.?context", re.IGNORECASE),
    re.compile(r"context.?window", re.IGNORECASE),
    re.compile(r"token.?limit.?exceed", re.IGNORECASE),
    re.compile(r"input.?too.?long", re.IGNORECASE),
    re.compile(r"max.?tokens.*exceed", re.IGNORECASE),
]

DEFAULT_TTL_SECONDS: int = 1800
DEFAULT_MAX_ENTRIES: int = 512


class LearnedLimit:
    __slots__ = ("recorded_at", "source", "tokens", "ttl_seconds")

    def __init__(
        self,
        tokens: int,
        recorded_at: float | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        source: str = "error",
    ) -> None:
        self.tokens = tokens
        self.recorded_at = recorded_at or time.time()
        self.ttl_seconds = ttl_seconds
        self.source = source

    @property
    def expires_at(self) -> float:
        return self.recorded_at + self.ttl_seconds

    @property
    def is_stale(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "recorded_at": self.recorded_at,
            "ttl_seconds": self.ttl_seconds,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LearnedLimit:
        tokens_val: int = int(str(data["tokens"]))
        recorded_val: float = float(str(data["recorded_at"]))
        ttl_raw = data.get("ttl_seconds")
        ttl_val: int = int(str(ttl_raw)) if ttl_raw is not None else DEFAULT_TTL_SECONDS
        source_raw = data.get("source")
        source_val: str = str(source_raw) if source_raw is not None else "error"
        return cls(
            tokens=tokens_val,
            recorded_at=recorded_val,
            ttl_seconds=ttl_val,
            source=source_val,
        )


class ContextWindowLearner:
    def __init__(
        self,
        persist_path: Path | str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._limits: dict[str, LearnedLimit] = {}
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._persist_path: Path | None = Path(persist_path) if persist_path else None
        if self._persist_path is not None:
            self._load_from_disk()

    def _key(self, provider_id: str, model_id: str) -> str:
        return f"{provider_id}:{model_id}"

    def get_effective_limit(
        self, provider_id: str, model_id: str, declared_limit: int
    ) -> int:
        key = self._key(provider_id, model_id)
        learned = self._limits.get(key)
        if learned is None or learned.is_stale:
            return declared_limit
        return min(learned.tokens, declared_limit)

    def record_from_error(
        self, provider_id: str, model_id: str, error_message: str, max_tokens: int | None = None
    ) -> bool:
        if not is_context_overflow(error_message):
            return False
        if max_tokens is not None and max_tokens > 0:
            learned_tokens = int(max_tokens * 0.85)
        else:
            return False
        key = self._key(provider_id, model_id)
        existing = self._limits.get(key)
        if existing is not None and not existing.is_stale and learned_tokens >= existing.tokens:
            return False
        self._limits[key] = LearnedLimit(
            tokens=learned_tokens,
            ttl_seconds=self._ttl_seconds,
            source="error",
        )
        logger.info(
            "learned context limit for %s:%s = %d tokens (from error)",
            provider_id,
            model_id,
            learned_tokens,
        )
        self._maybe_persist()
        return True

    def record_limit(
        self, provider_id: str, model_id: str, tokens: int, source: str = "explicit"
    ) -> None:
        key = self._key(provider_id, model_id)
        self._limits[key] = LearnedLimit(
            tokens=tokens,
            ttl_seconds=self._ttl_seconds,
            source=source,
        )
        self._maybe_persist()

    def clear_stale(self) -> int:
        before = len(self._limits)
        self._limits = {k: v for k, v in self._limits.items() if not v.is_stale}
        cleared = before - len(self._limits)
        if cleared > 0:
            self._maybe_persist()
        return cleared

    def get_all(self) -> dict[str, LearnedLimit]:
        self.clear_stale()
        return dict(self._limits)

    def _maybe_persist(self) -> None:
        if self._persist_path is None:
            return
        self._persist_to_disk()

    def _persist_to_disk(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {key: limit.to_dict() for key, limit in self._limits.items()}
            tmp_path = self._persist_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self._persist_path)
        except OSError:
            logger.debug("failed to persist context window limits", exc_info=True)

    def _load_from_disk(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            for key, value in raw.items():
                try:
                    self._limits[key] = LearnedLimit.from_dict(value)
                except (KeyError, TypeError, ValueError):
                    continue
            self.clear_stale()
        except (OSError, json.JSONDecodeError):
            logger.debug("failed to load context window limits", exc_info=True)


def is_context_overflow(error_message: str) -> bool:
    return any(pattern.search(error_message) for pattern in CONTEXT_OVERFLOW_PATTERNS)
