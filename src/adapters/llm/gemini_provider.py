"""Google Gemini LLM provider adapter (bring-your-own-key)."""

from __future__ import annotations

from typing import Sequence

from src.domain.exceptions import ConfigurationError, LLMProviderError
from src.domain.models import ChatMessage


class GeminiProvider:
    """LLM provider backed by the Google Gemini API."""

    name = "gemini"

    def __init__(self, api_key: str | None, model: str = "gemini-1.5-flash") -> None:
        if not api_key:
            raise ConfigurationError("Gemini API key is required (PROFESSORVGC_GEMINI_API_KEY)")
        self._model_name = model
        try:
            import google.generativeai as genai  # noqa: PLC0415 - lazy optional dep
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'google-generativeai' package is not installed. "
                "Run: pip install google-generativeai"
            ) from exc
        # google-generativeai's own py.typed stubs don't explicitly re-export
        # these two names from the package __init__, even though both are
        # real, documented, stable public API (verified live against the
        # actual SDK) — a stub-completeness gap in that library, not a bug
        # here.
        genai.configure(api_key=api_key)  # type: ignore[attr-defined]
        self._genai = genai

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        generation_config: dict[str, object] = {"temperature": temperature}
        if json_mode:
            generation_config["response_mime_type"] = "application/json"
        try:
            model = self._genai.GenerativeModel(  # type: ignore[attr-defined]
                model_name=self._model_name,
                system_instruction=system,
                generation_config=generation_config,  # type: ignore[arg-type]
            )
            history = [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [m.content],
                }
                for m in messages
            ]
            response = model.generate_content(history)
        except Exception as exc:  # noqa: BLE001 - SDK raises many concrete types
            raise LLMProviderError(f"Gemini request failed: {exc}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise LLMProviderError("Gemini returned an empty completion")
        return str(text)
