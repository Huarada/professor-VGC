"""Keep numerical investment out of free-form coaching; typed tables own it."""

from __future__ import annotations

import re

_STAT = r"(?:Atk|Def|SpA|SpD|Spe|Speed|Attack|Defense|Special Attack|Special Defense)"
_ALLOCATION = re.compile(rf"\b\d+\s+{_STAT}\b|\b{_STAT}\s*[:=]\s*\d+\b", re.IGNORECASE)
_INVESTMENT = re.compile(r"\b(?:EVs?|stat[ -]?points?|investment|spread|baseline|build|investimento|pontos)\b", re.IGNORECASE)
_HP_ALLOCATION = re.compile(r"\b\d+\s+HP\b|\bHP\s*[:=]\s*\d+\b", re.IGNORECASE)
_LEGACY_EV = re.compile(r"\b(?:252|256|510|512)\b")


def contains_numerical_investment(text: str) -> bool:
    """Recognize allocation notation, preserving observed percentages and HP damage.

    This is a narrow output-format guard, not a general factuality validator.
    Numerical investment belongs to the renderer of typed inference evidence.
    """
    plain = text.replace("*", "").replace("`", "")
    if _ALLOCATION.search(plain):
        return True
    for paragraph in plain.split("\n\n"):
        if _INVESTMENT.search(paragraph) and (
            _HP_ALLOCATION.search(paragraph) or _LEGACY_EV.search(paragraph)
            or re.search(r"\b\d+\s*/\s*\d+\b", paragraph)
        ):
            return True
    return False
