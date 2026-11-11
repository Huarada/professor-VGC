"""Certify marginal endpoints without enumerating every compatible joint family."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Literal

from src.domain.bo3_inference.candidate_space import joint_assignments
from src.domain.bo3_inference.models import (
    DamageObservation,
    DeclaredSet,
    InferenceCheckpoint,
    PointRange,
    RuleProfile,
    StatName,
)
from src.domain.bo3_inference.session import summarize
from src.services.bo3_inference.solver import (
    ProjectionBudget,
    SearchLimit,
    evaluate_batch,
    relevant_axes,
)


def boundary_bounds(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    observations: Sequence[DamageObservation], rules: RuleProfile, budget: ProjectionBudget,
    projection_limit: int, deadline: float, previous: InferenceCheckpoint | None = None,
) -> InferenceCheckpoint | None:
    """Reject an endpoint only after all legal completions reject its damage.

    A supported witness proves feasibility of an extreme; it does not count joint
    families. Budget exhaustion preserves only exclusions already fully proven.
    """
    if any(entry.nature is None for entry in declarations.values()):
        return None
    axes = relevant_axes(observations)
    initial = {key: summarize(key, entry, rules) for key, entry in declarations.items()}
    bounds = {key: dict(summary.stat_points) for key, summary in initial.items()}
    if previous:
        bounds.update({summary.pokemon_id: dict(summary.stat_points) for summary in previous.summaries})
    started = budget.projections
    evaluated = previous.evaluated_assignments if previous else 0

    def feasible(key: str, stat: StatName, value: int) -> bool:
        nonlocal evaluated
        fixed = dict(declarations)
        fixed[key] = declarations[key].model_copy(update={"stat_points": {**declarations[key].stat_points, stat: value}})
        iterator = joint_assignments(fixed, axes, rules)
        while True:
            if time.monotonic() >= min(deadline, budget.deadline):
                raise SearchLimit("Marginal-boundary time budget reached")
            batch = list(islice(iterator, 64))
            if not batch:
                return False
            if budget.projections - started + len(batch) * len(observations) > projection_limit:
                raise SearchLimit("Marginal-boundary projection budget reached")
            alive, uncertain, _ = evaluate_batch(batch, observations, budget)
            evaluated += len(batch)
            if set(alive) - uncertain:
                return True
            if uncertain:
                raise SearchLimit("Unsupported endpoint evidence cannot exclude a candidate")

    conflict = False
    complete = True
    try:
        for key in sorted(axes):
            for stat in sorted(axes[key]):
                if stat in declarations[key].stat_points:
                    continue
                original = bounds[key][stat]
                minimum = None
                for value in range(original.minimum, original.maximum + 1):
                    if feasible(key, stat, value):
                        minimum = value
                        break
                    if value < original.maximum:
                        bounds[key][stat] = PointRange(minimum=value + 1, maximum=original.maximum)
                if minimum is None:
                    conflict = True
                    break
                maximum = original.maximum
                for value in range(original.maximum, minimum - 1, -1):
                    if feasible(key, stat, value):
                        maximum = value
                        break
                    bounds[key][stat] = PointRange(minimum=minimum, maximum=value - 1)
                bounds[key][stat] = PointRange(minimum=minimum, maximum=maximum)
            if conflict:
                break
    except SearchLimit as exc:
        complete = False
        budget.factor_limit_reasons.setdefault(game, set()).add(str(exc))
        if all(bounds[key] == original.stat_points for key, original in initial.items()):
            return None
    summaries = []
    for key, baseline_summary in initial.items():
        current = bounds[key]
        current = {stat: PointRange(minimum=bound.minimum,
            maximum=min(bound.maximum, rules.total_budget - sum(other.minimum for name, other in current.items() if name != stat)))
            for stat, bound in current.items()}
        summaries.append(baseline_summary.model_copy(update={"stat_points": current,
            "basis": "necessary_local_bounds" if current != baseline_summary.stat_points else baseline_summary.basis}))
    return InferenceCheckpoint(id=f"g{game}:{phase}:boundaries", game=game, phase=phase,
        perspective=budget.options.perspective, outcome="conflict" if conflict else "ambiguous", coverage="bounded",
        evaluated_assignments=evaluated, observation_ids=tuple(sample.id for sample in observations),
        summaries=tuple(summaries), marginal_bounds_complete=complete)
