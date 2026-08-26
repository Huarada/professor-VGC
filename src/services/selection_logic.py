"""Pure selection-plan logic shared by every orchestration backend.

Includes a deterministic guardrail: a "matchup" only makes sense between two
Pokemon on OPPOSITE sides. When the model returns same-side or unknown pairs,
they are dropped and replaced by cross-side defaults, so the calc engine is
never asked to evaluate a Pokemon against its own teammate.
"""

from __future__ import annotations

import itertools
import json
from typing import Any, Mapping, Sequence

from src.domain.models import SelectionPlan


def plan_from_payload(data: dict[str, Any]) -> SelectionPlan:
    """Build a SelectionPlan from an already-decoded JSON mapping."""
    matchups = [
        (str(pair[0]), str(pair[1]))
        for pair in data.get("matchups", [])
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    ]
    return SelectionPlan(
        focus_species=[str(s) for s in data.get("focus_species", [])],
        matchups=matchups,
        rationale=str(data.get("rationale", "")),
    )


def cross_side_matchups(side_of: Mapping[str, str], limit: int = 6) -> list[tuple[str, str]]:
    """Enumerate ordered attacker->defender pairs across opposing sides."""
    pairs: list[tuple[str, str]] = []
    species = list(side_of.keys())
    for attacker, defender in itertools.permutations(species, 2):
        if side_of.get(attacker) != side_of.get(defender):
            pairs.append((attacker, defender))
    return pairs[:limit]


def fallback_plan(species: Sequence[str], side_of: Mapping[str, str] | None) -> SelectionPlan:
    """Deterministic degradation when the model output is unusable."""
    if side_of:
        matchups = cross_side_matchups(side_of)
    else:
        matchups = list(itertools.permutations(list(species)[:4], 2))
    return SelectionPlan(
        focus_species=list(species),
        matchups=matchups,
        rationale="Fallback: model output unusable; derived from the battle state.",
    )


def parse_selection(
    raw: str | dict[str, Any],
    species: Sequence[str],
    side_of: Mapping[str, str] | None = None,
) -> SelectionPlan:
    """Parse a raw model response (JSON string or dict) into a SelectionPlan."""
    if isinstance(raw, dict):
        try:
            return plan_from_payload(raw)
        except (TypeError, ValueError):
            return fallback_plan(species, side_of)
    try:
        data = json.loads(raw)
        return plan_from_payload(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback_plan(species, side_of)


def sanitize_plan(
    plan: SelectionPlan,
    species: Sequence[str],
    max_matchups: int,
    side_of: Mapping[str, str] | None = None,
) -> SelectionPlan:
    """Keep only real, cross-side matchups and cap the matchup count."""
    valid = set(species)
    focus = [s for s in plan.focus_species if s in valid] or list(species)
    matchups: list[tuple[str, str]] = []
    for a, b in plan.matchups:
        if a not in valid or b not in valid or a == b:
            continue
        # Enforce opposite sides when side membership is known.
        if side_of is not None and side_of.get(a) == side_of.get(b):
            continue
        matchups.append((a, b))
    if not matchups:
        if side_of:
            matchups = cross_side_matchups(side_of, limit=max_matchups)
        else:
            matchups = list(itertools.permutations(list(species)[:3], 2))
    return SelectionPlan(
        focus_species=focus,
        matchups=matchups[:max_matchups],
        rationale=plan.rationale,
    )


def build_selection_input(
    format_id: str,
    rosters: Mapping[str, Sequence[str]],
    question: str,
    outcome_summary: str = "",
) -> str:
    """Render the human-turn text for the selection stage (shared wording).

    ``rosters`` maps each player id to the species it brought, so the model can
    only pair Pokemon across opposing sides.
    """
    roster_lines = "\n".join(
        f"  {player}: {list(mons)}" for player, mons in rosters.items()
    )
    outcome_block = f"\nBattle result (ground truth):\n{outcome_summary}\n" if outcome_summary else ""
    return (
        f"Battle format: {format_id}\n"
        f"Rosters by side (only pair Pokemon from DIFFERENT sides):\n{roster_lines}\n"
        f"{outcome_block}"
        f"User question: {question or '(general analysis)'}\n"
        "Return the selection JSON."
    )
