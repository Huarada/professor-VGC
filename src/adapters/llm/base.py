"""Shared helpers for LLM provider adapters."""

from __future__ import annotations

from typing import Sequence

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
