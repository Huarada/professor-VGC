"""Google Gemini LLM provider adapter (bring-your-own-key)."""

from __future__ import annotations

import logging
import math
import time
from typing import Sequence

from src.adapters.llm.base import require_modern_gemini_model
from src.domain.exceptions import ConfigurationError, LLMProviderError
from src.domain.models import ChatMessage

_LOGGER = logging.getLogger(__name__)


class GeminiProvider:
    """LLM provider backed by the Google Gemini API."""

    name = "gemini"

    def __init__(self, api_key: str | None, model: str = "gemini-3.5-flash", *,
                 timeout_seconds: float = 90.0, total_timeout_seconds: float = 180.0) -> None:
        if not api_key:
            raise ConfigurationError("Gemini API key is required (PROFESSORVGC_GEMINI_API_KEY)")
        require_modern_gemini_model(model)
        if any(not math.isfinite(value) or value <= 0 for value in (timeout_seconds, total_timeout_seconds)):
            raise ConfigurationError("Gemini request and total timeouts must be finite positive seconds")
        self._model_name = model
        self._timeout_seconds = timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        try:
            import google.generativeai as genai  # noqa: PLC0415 - lazy optional dep
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'google-generativeai' package is not installed. "
                "Run: pip install google-generativeai"
            ) from exc
        # The SDK's stubs don't re-export these public names.
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
            from google.api_core.exceptions import (  # noqa: PLC0415 - lazy optional dep
                DeadlineExceeded,
                InternalServerError,
                ServiceUnavailable,
            )
            from google.generativeai.types import (
                RequestOptions,  # noqa: PLC0415 - lazy optional dep
            )
        except ImportError as exc:  # pragma: no cover - optional dependency boundary
            raise ConfigurationError("The Gemini SDK dependencies are not installed") from exc
        attempts = 0
        deadline = time.monotonic() + self._total_timeout_seconds
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
            last_failure: Exception | None = None
            for attempt in range(3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Local Gemini completion budget exhausted") from last_failure
                attempts += 1
                try:
                    # Own the retry budget instead of nesting the SDK's defaults.
                    response = model.generate_content(
                        history, request_options=RequestOptions(
                            retry=None, timeout=min(self._timeout_seconds, remaining)),
                    )
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Local Gemini completion budget exhausted")
                    break
                except (InternalServerError, ServiceUnavailable, DeadlineExceeded) as exc:
                    last_failure = exc
                    _LOGGER.warning("Gemini transient failure: model=%s attempt=%s error=%s",
                                    self._model_name, attempts, type(exc).__name__)
                    if attempt == 2:
                        raise
                    remaining = deadline - time.monotonic()
                    delay = 2 ** attempt
                    if remaining <= delay:
                        raise TimeoutError("Local Gemini completion budget exhausted") from exc
                    time.sleep(delay)
        except (DeadlineExceeded, TimeoutError) as exc:
            raise LLMProviderError(
                f"Gemini request timed out (model={self._model_name}, attempts={attempts}, "
                f"total budget={self._total_timeout_seconds:g}s). "
                "The provider did not complete within the configured deadline. Try again later."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - SDK raises many concrete types
            raise LLMProviderError(f"Gemini request failed: {exc}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise LLMProviderError("Gemini returned an empty completion")
        return str(text)
