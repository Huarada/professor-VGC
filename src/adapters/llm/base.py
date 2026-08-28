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
    """Raise ConfigurationError for a Gemini model below MIN_GEMINI_VERSION,
    BEFORE any network call — turns what would otherwise be a confusing
    "404 NOT_FOUND ... is not supported for generateContent" deep inside an
    SDK call stack into an immediate, actionable message instead.

    Defense in depth alongside ``Settings.gemini_model``'s own field
    validator (``src/config.py``, which this reuses ``parse_gemini_version``/
    ``MIN_GEMINI_VERSION`` from — one source of truth for the actual version
    check): that validator catches a bad value the moment a FRESH ``Settings``
    is constructed, but reported live, an already-running Streamlit process
    (its Container/Settings cached once in st.session_state at first use —
    see src/ui/app.py's _get_container) kept using a stale gemini-1.5-flash
    value from before PROFESSORVGC_GEMINI_MODEL's own default was updated,
    for the lifetime of that already-constructed, never-revalidated object.
    This guard, called from every construction site that turns a model-id
    string into a real Gemini client (native GeminiProvider,
    langchain_provider.build_chat_model, adk_provider.build_adk_model),
    doesn't by itself fix an already-stale cached session (only restarting
    the process / clicking "Reset conversation" does that — see the message
    below) — it makes the NEXT time this class of drift happens immediately
    diagnosable instead of a bare SDK error naming a mysterious model id.
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
