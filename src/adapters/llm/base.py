"""Shared helpers for LLM provider adapters."""

from __future__ import annotations

from typing import Sequence

from src.config import MIN_GEMINI_VERSION, parse_gemini_version
from src.domain.exceptions import ConfigurationError
from src.domain.models import ChatMessage


def to_role_dicts(
    messages: Sequence[ChatMessage],
    *,
    user_role: str = "user",
    assistant_role: str = "assistant",
) -> list[dict[str, str]]:
    """Map domain ChatMessages to a provider's ``{role, content}`` list."""
    role_map = {"user": user_role, "assistant": assistant_role, "system": user_role}
    return [
        {"role": role_map.get(m.role, user_role), "content": m.content}
        for m in messages
    ]


def require_modern_gemini_model(model: str) -> None:
    """Raise ConfigurationError for a Gemini model below ``MIN_GEMINI_VERSION``
    before any network call. Complements the Settings validator for processes
    holding an older cached Settings.
    """
    version = parse_gemini_version(model)
    if version is None or version < MIN_GEMINI_VERSION:
        min_str = ".".join(str(p) for p in MIN_GEMINI_VERSION)
        raise ConfigurationError(
            f"PROFESSORVGC_GEMINI_MODEL='{model}' is not supported — this "
            f"project requires Gemini {min_str} or newer (e.g. "
            f"'gemini-3.5-flash'). If you already updated this setting, an "
            f"already-running Streamlit session won't pick it up on its "
            f"own (its Container is cached once at first use) — click "
            f"'Reset conversation' in the sidebar, or fully restart "
            f"`streamlit run`, then reload the page."
        )
