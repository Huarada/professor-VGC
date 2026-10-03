"""Google ADK model factory (:func:`build_adk_model`) for the ADK backend.

- gemini: ADK's native Gemini model; the key goes in ``GOOGLE_API_KEY``
  (ADK has no per-agent key parameter).
- openai: ADK's documented ``LiteLlm`` wrapper; key in ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from src.adapters.llm.base import require_modern_gemini_model
from src.config import Settings
from src.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    # google-adk / litellm are imported lazily (ConfigurationError if missing).
    from google.adk.models import BaseLlm

_SUPPORTED = ("openai", "gemini")


def build_adk_model(provider: str, settings: Settings) -> "str | BaseLlm":
    """BYOK factory returning the ``model=`` argument for an ADK ``Agent``."""
    name = (provider or settings.default_provider).lower()
    if name not in _SUPPORTED:
        raise ConfigurationError(
            f"Unknown provider '{name}'. Available: {list(_SUPPORTED)}"
        )
    if name == "gemini":
        if not settings.gemini_api_key:
            raise ConfigurationError(
                "Gemini API key required (PROFESSORVGC_GEMINI_API_KEY)"
            )
        require_modern_gemini_model(settings.gemini_model)
        os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key
        # Explicit Gemini model with bounded retries: ADK's default retries a 503 for
        # minutes, which looks like a hang.
        from google.adk.models import Gemini
        from google.genai import types as genai_types

        return Gemini(
            model=settings.gemini_model,
            retry_options=genai_types.HttpRetryOptions(
                attempts=3, initial_delay=1.0, max_delay=8.0
            ),
        )
    if not settings.openai_api_key:
        raise ConfigurationError("OpenAI API key required (PROFESSORVGC_OPENAI_API_KEY)")
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ConfigurationError(
            "The 'litellm' package is not installed (ADK's own path to OpenAI "
            "models). Run: pip install litellm"
        ) from exc
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    # LiteLLM's own routing convention: "<provider>/<model-id>".
    return LiteLlm(model=f"openai/{settings.openai_model}")
