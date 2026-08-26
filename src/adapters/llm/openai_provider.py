"""OpenAI LLM provider adapter (bring-your-own-key)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from src.adapters.llm.base import to_role_dicts
from src.domain.exceptions import ConfigurationError, LLMProviderError
from src.domain.models import ChatMessage

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion


class OpenAIProvider:
    """LLM provider backed by the OpenAI Chat Completions API."""

    name = "openai"

    def __init__(self, api_key: str | None, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise ConfigurationError("OpenAI API key is required (PROFESSORVGC_OPENAI_API_KEY)")
        self._model = model
        try:
            from openai import OpenAI  # noqa: PLC0415 - lazy optional dependency
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'openai' package is not installed. Run: pip install openai"
            ) from exc
        self._client = OpenAI(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        payload = [{"role": "system", "content": system}, *to_role_dicts(messages)]
        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": payload,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            # The SDK's `create` overloads are keyed on a literal `stream=`
            # value it can't resolve from a dynamically-built **kwargs dict
            # (response_format is only added conditionally above) — the
            # explicit ChatCompletion annotation below is what actually
            # restores real typing for everything after this call.
            response: ChatCompletion = self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
        except Exception as exc:  # noqa: BLE001 - SDK raises many concrete types
            raise LLMProviderError(f"OpenAI request failed: {exc}") from exc
        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("OpenAI returned an empty completion")
        return content
