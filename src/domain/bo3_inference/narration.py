"""Keep numerical investment out of free-form coaching; typed tables own it."""

from __future__ import annotations

import re
from typing import Any

_STAT = r"(?:Special Attack|Special Defense|Ataque(?: Especial)?|Defesa(?: Especial)?|Velocidade|Atk|Def|SpA|SpD|Spe|Speed|Attack|Defense)"
_ALLOCATION = re.compile(rf"\b\d+\s+{_STAT}\b|\b{_STAT}\s*[:=|]\s*\d+\b", re.IGNORECASE)
_INVESTMENT = re.compile(r"\b(?:EVs?|stat[ -]?points?|investment|spread|baseline|build|investimento|pontos)\b", re.IGNORECASE)
_HP_ALLOCATION = re.compile(r"\b\d+\s+HP\b|\bHP\s*[:=|]\s*\d+\b", re.IGNORECASE)
_EV_ALLOCATION = re.compile(r"\b\d+\s*EVs?\b|\bEVs?\s*[:=|]\s*\d+\b", re.IGNORECASE)
_LEGACY_EV = re.compile(r"\b(?:252|256|510|512)\b")


def contains_numerical_investment(text: str) -> bool:
    """Recognize allocation notation, preserving observed percentages and HP damage.

    This is a narrow output-format guard, not a general factuality validator.
    Numerical investment belongs to the renderer of typed inference evidence.
    """
    plain = text.replace("*", "").replace("`", "")
    # Mechanical quantities are not investment, even in an assumed-set paragraph.
    plain = re.sub(r"\b(?:dealt|removed|lost|healed|recovered|causou|perdeu|curou)\s+\d+\s*HP\b",
                   "", plain, flags=re.IGNORECASE)
    plain = re.sub(rf"\b(?:actual|effective|base|raw|final)\s+{_STAT}\s*[:=]\s*\d+\b",
                   "", plain, flags=re.IGNORECASE)
    if _ALLOCATION.search(plain) or _EV_ALLOCATION.search(plain):
        return True
    for paragraph in plain.split("\n\n"):
        if _INVESTMENT.search(paragraph) and (
            _HP_ALLOCATION.search(paragraph) or _LEGACY_EV.search(paragraph)
            or re.search(r"\b\d+\s*/\s*\d+\b", paragraph)
        ):
            return True
    return False


_ATTRIBUTE_CLAIM = re.compile(
    rf"\b{_STAT}(?:\s+(?:stat|value))?(?:\s+(?:of|was|is|de|era))?\s*[:=]?\s+\d+\b"
    r"(?!\s*(?:stages?\b|%))", re.IGNORECASE,
)


def contains_numerical_attributes(text: str) -> bool:
    """Card-stat claims belong to typed tables; damage percentages/stages do not."""
    return _ATTRIBUTE_CLAIM.search(text.replace("*", "").replace("`", "")) is not None


UNVALIDATED_INVESTMENT_NOTICE = (
    "The model's numerical investment or attribute claims could not be validated, so its narrative was omitted. "
    "Review the deterministic battle evidence and the BO3 inference tables where available. "
    "This narrative does not establish any exact hidden spread."
)

_INVESTMENT_FIELDS = frozenset({
    "evs", "ivs", "spreads", "top_spreads", "top_spread_evs", "top_spread_nature",
    "stat_points", "declared_stat_points", "joint_examples", "attributes",
})


def coaching_context(value: Any) -> Any:
    """Project BO3 JSON for narration; retain the complete evidence in result DTOs.

    Numerical investment is already displayed in typed tables. Sending both legacy
    priors and candidate allocations back to the narrator invites invented spreads.
    Damage numbers, causal state, declared traits, provenance and coverage remain.
    """
    if isinstance(value, dict):
        if "faster_speed" in value or "slower_speed" in value:
            return {"basis": "assumed_set_speed_projection_not_observed_or_inferred"}
        return {key: entry if key == "question" else coaching_context(entry)
                for key, entry in value.items() if key not in _INVESTMENT_FIELDS}
    if isinstance(value, (list, tuple)):
        return [coaching_context(entry) for entry in value]
    if isinstance(value, str) and (contains_numerical_investment(value) or contains_numerical_attributes(value)):
        return "[Numerical investment omitted; use the deterministic tables.]"
    return value
