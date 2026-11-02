"""Validated inference findings for coaching, distinct from metagame priors."""

import re
from typing import Literal

from pydantic import Field

from src.domain.bo3_inference.models import (
    InferenceCheckpoint,
    PointRange,
    RangeReduction,
    StatName,
    Value,
)
from src.domain.bo3_inference.range_reduction import compare_ranges


class FormFinding(Value):
    form: str
    level: Literal[50] = 50
    units: Literal["level_50_attribute_values"] = "level_50_attribute_values"
    unboosted_attributes: dict[StatName, PointRange]
    forme_ability: str | None = None
    range_reductions: dict[StatName, RangeReduction] = Field(default_factory=dict)
    meaning: Literal["calculated_outer_ranges_not_observed_stats"] = "calculated_outer_ranges_not_observed_stats"


class PokemonFinding(Value):
    pokemon_id: str
    species: str
    natures: tuple[str, ...]
    basis: str
    declared_base_ability: str | None
    declared_item: str | None = None
    declared_moves: tuple[str, ...] = ()
    nature_is_declared: bool = False
    declared_native_bounds: dict[StatName, PointRange]
    constrained_native_bounds: dict[StatName, PointRange]
    unresolved_axes: tuple[StatName, ...]
    complete_spread_known: bool
    range_reductions: dict[StatName, RangeReduction]
    forms: tuple[FormFinding, ...]


class CheckpointFinding(Value):
    game: int
    phase: Literal["before_game", "after_game"]
    coverage: str
    outcome: str
    eligible_damage_observations: int
    complete_local_constraints: int
    flagged_damage_observations: int
    partial_local_constraints: int = 0
    units: Literal["native_champions_stat_points"] = "native_champions_stat_points"
    marginal_extrema_are_not_joint_examples: Literal[True] = True
    pokemon: tuple[PokemonFinding, ...]


def interpret_checkpoint(checkpoint: InferenceCheckpoint) -> CheckpointFinding:
    """Describe only validated bounds; a card attribute is never a hidden EV fact."""
    findings = []
    for summary in checkpoint.summaries:
        declared = {stat: PointRange(minimum=bound.minimum, maximum=bound.maximum)
                    for stat, bound in summary.stat_points.items() if stat in summary.declared_stat_points}
        constrained = {stat: PointRange(minimum=bound.minimum, maximum=bound.maximum) for stat, bound in summary.stat_points.items()
                       if summary.basis != "declarations" and stat not in declared
                       and (bound.minimum, bound.maximum) != (0, 32)}
        unresolved = tuple(stat for stat, bound in summary.stat_points.items() if bound.minimum != bound.maximum)
        known = (len(summary.stat_points) == 6 and not unresolved and len(summary.natures) == 1 and checkpoint.outcome != "conflict"
                 and (checkpoint.coverage == "exhaustive" or len(declared) == 6))
        findings.append(PokemonFinding(
            pokemon_id=summary.pokemon_id, species=summary.species, natures=summary.natures,
            basis=summary.basis, declared_base_ability=summary.declared_ability,
            declared_item=summary.declared_item, declared_moves=summary.declared_moves,
            nature_is_declared=summary.nature_declared,
            declared_native_bounds=declared, constrained_native_bounds=constrained,
            unresolved_axes=unresolved, complete_spread_known=known,
            range_reductions={stat: change for stat, initial in getattr(summary, "baseline_stat_points", {}).items()
                if checkpoint.outcome != "conflict" and stat in summary.stat_points
                and (change := compare_ranges(initial, summary.stat_points[stat])) is not None},
            forms=tuple(FormFinding(form=entry.species, unboosted_attributes={
                                        stat: PointRange(minimum=bound.minimum, maximum=bound.maximum)
                                        for stat, bound in entry.attributes.items()},
                                    forme_ability=getattr(entry, "forme_ability", None),
                                    range_reductions={stat: change for stat, initial in getattr(entry, "baseline_attributes", {}).items()
                                        if checkpoint.outcome != "conflict" and stat in entry.attributes
                                        and (change := compare_ranges(initial, entry.attributes[stat])) is not None})
                        for entry in summary.attributes if entry.supported),
        ))
    flagged = {reason.split(": ", 1)[0] for reason in checkpoint.unsupported
               if re.match(r"^g\d+:e\d+:p[12]:", reason)}
    return CheckpointFinding(game=checkpoint.game, phase=checkpoint.phase, coverage=checkpoint.coverage,
        outcome=checkpoint.outcome, eligible_damage_observations=len(checkpoint.observation_ids),
        complete_local_constraints=len(checkpoint.local_constraints), flagged_damage_observations=len(flagged),
        partial_local_constraints=len(checkpoint.partial_local_constraints),
        pokemon=tuple(findings))
