"""BYOK factory for the LangChain backend's chat model (:func:`build_chat_model`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from src.adapters.llm.base import require_modern_gemini_model
from src.config import Settings
from src.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    # langchain_core is imported lazily (ConfigurationError if missing).
    from langchain_core.language_models import BaseChatModel

_SUPPORTED = ("openai", "gemini")


def build_chat_model(provider: str, settings: Settings) -> BaseChatModel:
    """BYOK factory returning a concrete LangChain chat model."""
    name = (provider or settings.default_provider).lower()
    if name not in _SUPPORTED:
        raise ConfigurationError(
            f"Unknown provider '{name}'. Available: {list(_SUPPORTED)}"
        )
    if name == "openai":
        if not settings.openai_api_key:
            raise ConfigurationError("OpenAI API key required (PROFESSORVGC_OPENAI_API_KEY)")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "langchain-openai is not installed. Run: pip install langchain-openai"
            ) from exc
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=SecretStr(settings.openai_api_key),
            temperature=settings.llm_temperature,
        )
    if not settings.gemini_api_key:
        raise ConfigurationError("Gemini API key required (PROFESSORVGC_GEMINI_API_KEY)")
    require_modern_gemini_model(settings.gemini_model)
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ConfigurationError(
            "langchain-google-genai is not installed. "
            "Run: pip install langchain-google-genai"
        ) from exc
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=settings.llm_temperature,
    )
