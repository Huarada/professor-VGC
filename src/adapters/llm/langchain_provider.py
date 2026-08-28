"""LangChain integration for the LLM boundary.

Two responsibilities, both confined to the adapters layer (LangChain must never
leak into domain or services signatures):

1. :class:`LangChainLLMProvider` — implements the domain
   :class:`~src.domain.interfaces.LLMProvider` Protocol on top of ANY LangChain
   ``BaseChatModel``.
2. :func:`build_chat_model` — a BYOK factory that instantiates the concrete
   LangChain chat model (OpenAI / Gemini) from :class:`~src.config.Settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from pydantic import SecretStr

from src.adapters.llm.base import require_modern_gemini_model
from src.config import Settings
from src.domain.exceptions import ConfigurationError, LLMProviderError
from src.domain.models import ChatMessage

if TYPE_CHECKING:
    # Type-checking only — every runtime use stays a local, lazy import
    # (below) so this module remains importable even where langchain_core
    # isn't installed, despite it being a normal top-level dependency in
    # pyproject.toml/requirements.txt: this adapter is meant to degrade
    # gracefully (ConfigurationError, not an ImportError at module import
    # time) the same way the OpenAI/Gemini adapters already do.
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from langchain_core.runnables import Runnable

_SUPPORTED = ("openai", "gemini")


def to_lc_messages(messages: Sequence[ChatMessage]) -> list[BaseMessage]:
    """Map domain ChatMessages to LangChain message objects."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    mapped: list[BaseMessage] = []
    for message in messages:
        if message.role == "assistant":
            mapped.append(AIMessage(content=message.content))
        elif message.role == "system":
            mapped.append(SystemMessage(content=message.content))
        else:
            mapped.append(HumanMessage(content=message.content))
    return mapped


def _content_to_text(content: object) -> str:
    """Coerce a LangChain message content (str or content blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return "".join(parts)
    return str(content)


class LangChainLLMProvider:
    """Domain :class:`LLMProvider` backed by a LangChain ``BaseChatModel``."""

    def __init__(self, chat_model: BaseChatModel, name: str = "langchain") -> None:
        self.name = name
        self._model = chat_model

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        from langchain_core.messages import SystemMessage

        payload = [SystemMessage(content=system), *to_lc_messages(messages)]
        model: Runnable[Any, Any] = self._model
        if json_mode:
            model = self._as_json_model(self._model)
        try:
            response = model.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - many SDK exception types
            raise LLMProviderError(f"LangChain model call failed: {exc}") from exc
        text = _content_to_text(getattr(response, "content", response))
        if not text:
            raise LLMProviderError("LangChain model returned empty content")
        return text

    @staticmethod
    def _as_json_model(model: BaseChatModel) -> Runnable[Any, Any]:
        """Best-effort: request JSON output where the backend supports it."""
        try:
            return model.bind(response_format={"type": "json_object"})
        except Exception:  # noqa: BLE001 - not all models support binding
            return model


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
