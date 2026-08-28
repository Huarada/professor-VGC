"""Shared helpers for LLM provider adapters."""

from __future__ import annotations

import re
from typing import Sequence

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


_GEMINI_MODEL_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?")
# This project's own requirement (not a Google-imposed floor): only Gemini
# 3.5 and newer. Enforced here, once, and called from every construction
# site that turns a model-id string into a real Gemini client (native
# GeminiProvider, langchain_provider.build_chat_model, adk_provider.
# build_adk_model) — a single source of truth, same principle as every
# other "push the guarantee into deterministic code" decision in this
# codebase (see ADR-010).
MIN_GEMINI_VERSION = (3, 5)


def require_modern_gemini_model(model: str) -> None:
    """Raise ConfigurationError for a Gemini model below MIN_GEMINI_VERSION,
    BEFORE any network call — turns what would otherwise be a confusing
    "404 NOT_FOUND ... is not supported for generateContent" deep inside an
    SDK call stack into an immediate, actionable message instead.

    Reported live: a already-running Streamlit process (its Container/
    Settings cached once in st.session_state at first use — see
    src/ui/app.py's _get_container) kept using a stale gemini-1.5-flash
    default from before PROFESSORVGC_GEMINI_MODEL's own default was
    updated to gemini-3.5-flash, surfacing as exactly that 404. This guard
    does not by itself fix an already-stale cached session (only
    restarting the process / clicking "Reset conversation" does that — see
    the message below) — it makes the NEXT time this happens immediately
    diagnosable instead of a bare SDK error naming a mysterious model id.
    """
    match = _GEMINI_MODEL_RE.match((model or "").strip().lower())
    version = (int(match.group(1)), int(match.group(2) or 0)) if match else None
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
