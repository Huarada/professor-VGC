"""Format computed spread findings alongside coaching without LLM arithmetic."""

from collections.abc import Sequence

from src.domain.bo3_inference.citations import bound_evidence, describe_hit
from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import (
    DamageCitation,
    InferenceCheckpoint,
    StatName,
)

_LABELS: dict[StatName, str] = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}


def _hit(observation_id: str) -> str:
    game, event = observation_id.split(":")[:2]
    return f"game {game[1:]} event {event[1:]}"


def stat_spread_commentary(checkpoint: InferenceCheckpoint | None,
                           citations: Sequence[DamageCitation] = ()) -> str:
    """Show proven ranges or a clear unresolved result, never an invented set."""
    if checkpoint is None:
        return ""
    findings = interpret_checkpoint(checkpoint)
    lines = ["**Computed stat-spread findings**"]
    if findings.outcome == "conflict":
        lines.append("The modeled evidence conflicts. The displayed ranges retain earlier valid bounds; "
                     "they do not resolve this conflict or identify a new spread.")
    informative = [entry for entry in findings.pokemon
                   if entry.constrained_native_bounds or entry.declared_native_bounds]
    if not informative:
        lines.append("No original stat-point spread was identified at this checkpoint. Known natures, items, "
                     "abilities and moves are teamsheet facts, not an identified IV/EV spread.")
    else:
        summaries = {summary.pokemon_id: summary for summary in checkpoint.summaries}
        for entry in informative:
            player = entry.pokemon_id.rsplit(":slot", 1)[0]
            for label, bounds in (("declared native points", entry.declared_native_bounds),
                                  ("compatible native-point bounds", entry.constrained_native_bounds)):
                if not bounds:
                    continue
                values = ", ".join(f"{_LABELS[stat]} {bound.minimum}" if bound.minimum == bound.maximum
                                   else f"{_LABELS[stat]} {bound.minimum}-{bound.maximum}"
                                   for stat, bound in bounds.items())
                lines.append(f"- **{entry.species} ({player})** — {label}: {values}. "
                             + ("The full six-stat spread is fixed within the supplied model."
                                if entry.complete_spread_known else "Other stat-point choices may remain open."))
            summary = summaries.get(entry.pokemon_id)
            if summary is None or not entry.constrained_native_bounds:
                continue
            if summary.excluded_points:
                lines.append("  Also impossible inside these ranges: " + "; ".join(
                    f"{_LABELS[stat]} {', '.join(map(str, values))}" for stat, values in summary.excluded_points.items()) + ".")
            fragile = {stat: hits for stat, hits in summary.single_observation_bounds.items()
                       if stat in entry.constrained_native_bounds}
            if fragile:
                lines.append("  **Single-observation dependency:** " + "; ".join(
                    f"{_LABELS[stat]} ({', '.join(map(_hit, hits))})" for stat, hits in fragile.items())
                    + ". These were leave-one-out dependencies when checked; later hits may add redundancy.")
    lines.append(f"The calculation used {findings.eligible_damage_observations} eligible damage observations and "
                 f"{findings.complete_local_constraints} certified damage constraints. "
                 "Local constraints can reject joint combinations without narrowing every individual stat.")
    if findings.partial_local_constraints:
        lines.append(f"{findings.partial_local_constraints} damage observations participated in budget-limited local searches "
                     "that supplied certified boundary exclusions. Counts can overlap with completed local comparisons; "
                     "these are necessary outer bounds, not exhaustive joint spreads.")
    if findings.flagged_damage_observations:
        lines.append(f"{findings.flagged_damage_observations} damage observations have modeling limitations; "
                     "they cannot justify a more precise spread without additional support.")
    reductions = []
    for entry in findings.pokemon:
        player = entry.pokemon_id.rsplit(":slot", 1)[0]
        for stat, change in entry.range_reductions.items():
            if change.removed_integer_values:
                detail = (f"- **{entry.species} ({player}), {_LABELS[stat]}**: "
                    f"{change.baseline.minimum}-{change.baseline.maximum} -> {change.current.minimum}-{change.current.maximum} "
                    f"native points; **{change.reduction_percent}%** fewer integer possibilities in this interval.")
                summary = next((item for item in checkpoint.summaries if item.pokemon_id == entry.pokemon_id), None)
                if summary is not None:
                    label, hits = bound_evidence(checkpoint, summary, stat, citations)
                    if hits:
                        detail += "\n  - " + label + ": " + "; ".join(describe_hit(hit) for hit in hits) + "."
                reductions.append(detail)
    if reductions:
        lines.append("**Range reduction from initial declarations**")
        lines.extend(reductions)
        lines.append("These reductions measure outer-interval narrowing, not confidence or win probability.")
    elif findings.outcome != "conflict" and any(entry.range_reductions for entry in findings.pokemon):
        lines.append("Native stat-point interval reduction: **0%** versus the initial teamsheet bounds.")
        lines.append("The numeric ranges below are unchanged theoretical baselines, not recovered original spreads. "
                     "Damage constrains combinations of offensive stats, defenses and HP; it may leave each individual range unchanged.")
    if any(entry.forms for entry in findings.pokemon):
        lines.append("**Numeric stats - level 50, before battle modifiers**")
        lines.append("These are calculated stat values, not Champions stat-point allocations. "
                     "A single number is fixed within the supplied bounds; a range remains unresolved.")
        for entry in findings.pokemon:
            player = entry.pokemon_id.rsplit(":slot", 1)[0]
            for form in entry.forms:
                stat_values = []
                for stat, label in _LABELS.items():
                    bound = form.unboosted_attributes[stat]
                    value = str(bound.minimum) if bound.minimum == bound.maximum else f"{bound.minimum}-{bound.maximum}"
                    reduction = form.range_reductions.get(stat)
                    note = (f" (initial {reduction.baseline.minimum}-{reduction.baseline.maximum}; "
                            f"-{reduction.reduction_percent}% interval)" if reduction and reduction.removed_integer_values else "")
                    stat_values.append(f"{label} **{value}**{note}")
                lines.append(f"- **{form.form} ({player})**: " + " / ".join(stat_values) + ".")
    lines.append("The six level-50 attribute ranges are calculated outer bounds, not observed battle stats "
                 "or EV allocations. Items, abilities, boosts and field effects can change battle values. "
                 "Marginal range endpoints cannot be combined into one assumed set.")
    for entry in findings.pokemon:
        player = entry.pokemon_id.rsplit(":slot", 1)[0]
        for form in entry.forms:
            if form.forme_ability:
                lines.append(f"- **{form.form} ({player})** uses **{form.forme_ability}** in this forme; "
                             "the base-form teamsheet ability is a separate fact.")
    return "\n\n".join(lines)
