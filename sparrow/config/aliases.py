from __future__ import annotations

DEFAULT_ALIASES: dict[str, str] = {
    "gpt-4o": "kilo/nvidia/nemotron-3-super-120b-a12b:free",
    "gpt-4o-mini": "kilo/openrouter/free",
    "o4-mini": "ovh/gpt-oss-120b",
    "claude-3.5-sonnet": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "claude-3-haiku": "pollinations/openai-fast",
    "llama-3.3-70b": "ovh/Meta-Llama-3_3-70B-Instruct",
    "llama-3.1-8b": "pollinations/openai-fast",
    "deepseek-r1": "kilo/deepseek/deepseek-r1:free",
    "gemini-2.5-flash": "kilo/google/gemini-2.5-flash:free",
    "mistral-small": "kilo/mistralai/mistral-small-3.1-24b-instruct:free",
    "auto": "fair",
}

class AliasResolver:

    def __init__(self, custom_aliases: dict[str, str] | None = None) -> None:
        self._aliases = {**DEFAULT_ALIASES}
        if custom_aliases:
            self._aliases.update(custom_aliases)

    def resolve(self, model: str) -> str:
        return self._aliases.get(model, model)

    def add_alias(self, alias: str, target: str) -> None:
        self._aliases[alias] = target

    def list_aliases(self) -> dict[str, str]:
        return self._aliases.copy()
