from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TASK_PATTERNS: dict[str, list[str]] = {
    "code": [
        r"\b(?:def |class |import |from |async |await |return |if |for |while )\b",
        r"\b(?:function |const |let |var |=>|export )\b",
        r"```(?:python|javascript|typescript|go|rust|java|c\+\+|ruby|php|swift|kotlin)",
        r"\b(?:bug|fix|error|debug|refactor|implement|code)\b",
    ],
    "creative": [
        r"\b(?:write|story|poem|creative|imagine|fiction|narrative)\b",
        r"\b(?:draft|compose|essay|article|blog|content)\b",
    ],
    "analysis": [
        r"\b(?:analyze|explain|compare|evaluate|review|assess)\b",
        r"\b(?:what|why|how|when|where|who)\b.*\?",
        r"\b(?:summary|summarize|overview|report)\b",
    ],
    "math": [
        r"\b(?:calculate|solve|equation|formula|proof|theorem)\b",
        r"\b\d+\s*[+\-*/÷x=]\s*\d+\b",
        r"\b(?:integral|derivative|matrix|vector|probability)\b",
    ],
    "translation": [
        r"\b(?:translate|traduzir|traducir|übersetzen|traduire)\b",
        r"\b(?:in english|em português|en español|auf deutsch)\b",
    ],
    "chat": [
        r"^(?:hi|hello|hey|ola|olá|oi|salut|hallo)\b",
        r"\b(?:thanks|obrigado|gracias|merci|danke)\b",
        r"\b(?:how are you|como vai|qué tal)\b",
    ],
}

_TASK_FALLBACK = "chat"


@dataclass
class CapabilityScore:
    model_id: str
    provider_id: str
    task_score: float = 0.0
    quality_score: float = 0.0
    context_fit: float = 0.0
    latency_bonus: float = 0.0
    total: float = 0.0

    def compute(self) -> float:
        self.total = (
            self.task_score * 0.40
            + self.quality_score * 0.25
            + self.context_fit * 0.20
            + self.latency_bonus * 0.15
        )
        return self.total


@dataclass
class TaskHint:
    task_type: str
    confidence: float
    raw_text: str = ""


@dataclass
class ModelCapability:
    model_id: str
    provider_id: str
    task_scores: dict[str, float] = field(default_factory=dict)
    context_window: int = 128000
    quality: float = 5.0
    avg_latency_ms: float = 0.0

    def score_for_task(self, task_type: str, context_tokens: int = 0) -> CapabilityScore:
        cs = CapabilityScore(
            model_id=self.model_id,
            provider_id=self.provider_id,
            task_score=self.task_scores.get(task_type, 0.5),
            quality_score=self.quality / 10.0,
            latency_bonus=max(0.0, 1.0 - self.avg_latency_ms / 10000.0),
        )
        if context_tokens > 0:
            ratio = context_tokens / max(self.context_window, 1)
            cs.context_fit = max(0.0, 1.0 - ratio) if ratio <= 1.0 else 0.0
        else:
            cs.context_fit = 1.0
        cs.compute()
        return cs


class CapabilityScorer:
    def __init__(self, models_config_path: Path | str | None = None) -> None:
        self._models: dict[str, ModelCapability] = {}
        self._task_patterns = _TASK_PATTERNS
        self._task_fallback = _TASK_FALLBACK
        if models_config_path:
            self._load_models_config(Path(models_config_path))

    def _load_models_config(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        models = data if isinstance(data, list) else data.get("models", [])
        for m in models:
            if not isinstance(m, dict):
                continue
            mid = m.get("id", "")
            if not mid:
                continue
            quality_raw = m.get("quality_score", m.get("quality", 5))
            quality = float(quality_raw if quality_raw is not None else 5)
            ctx_raw = m.get("context_window", 128000)
            ctx = int(ctx_raw if ctx_raw is not None else 128000)
            provider_raw = m.get("owned_by", m.get("provider", "unknown"))
            provider = str(provider_raw if provider_raw is not None else "unknown")
            task_scores: dict[str, float] = {}
            if "capabilities" in m and isinstance(m["capabilities"], dict):
                for task, score in m["capabilities"].items():
                    if isinstance(score, (int, float)):
                        task_scores[task] = float(score)
            self._models[mid] = ModelCapability(
                model_id=mid,
                provider_id=provider,
                task_scores=task_scores,
                context_window=ctx,
                quality=quality,
            )

    def detect_task(self, messages: list[dict[str, Any]]) -> TaskHint:
        text = self._extract_text(messages)
        if not text:
            return TaskHint(task_type=self._task_fallback, confidence=0.0, raw_text="")
        scores: dict[str, float] = {}
        for task, patterns in self._task_patterns.items():
            hits = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
            scores[task] = hits / len(patterns) if patterns else 0.0
        best_task = max(scores, key=lambda k: scores[k])
        best_score = scores[best_task]
        if best_score < 0.05:
            return TaskHint(task_type=self._task_fallback, confidence=0.1, raw_text=text[:200])
        return TaskHint(task_type=best_task, confidence=min(best_score, 1.0), raw_text=text[:200])

    def _extract_text(self, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for msg in messages[-5:]:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
        return "\n".join(parts)

    def score_models(
        self,
        task_type: str,
        context_tokens: int = 0,
        model_ids: list[str] | None = None,
    ) -> list[CapabilityScore]:
        candidates = list(self._models.values())
        if model_ids:
            candidates = [m for m in candidates if m.model_id in model_ids]
        scores = [m.score_for_task(task_type, context_tokens) for m in candidates]
        scores.sort(key=lambda s: s.total, reverse=True)
        return scores

    def _estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", ""))
        return max(1, total_chars // 4)
