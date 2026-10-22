"""Exhaustive local constraints supply sound outer bounds, never global joint samples."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Literal

from src.domain.bo3_inference.models import (
    DamageObservation,
    DeclaredSet,
    InferenceCheckpoint,
    RuleProfile,
    StatName,
)
from src.services.bo3_inference.solver import (
    ProjectionBudget,
    enumerate_constraints,
    relevant_axes,
)


def space_bound(
    declarations: Mapping[str, DeclaredSet], axes: Mapping[str, set[StatName]], rules: RuleProfile,
) -> int:
    """Conservative size bound; do not enumerate profiles simply to count them."""
    count = 1
    for key, relevant in axes.items():
        entry = declarations[key]
        count *= 1 if entry.nature else len(rules.natures)
        for axis in relevant:
            if axis not in entry.stat_points:
                count *= rules.stat_cap + 1
        if count > 100000:
            return count
    return count


def local_bounds(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    observations: Sequence[DamageObservation], rules: RuleProfile, budget: ProjectionBudget,
) -> list[InferenceCheckpoint]:
    """Complete affordable factors first; incomplete searches prove no new bounds."""
    proofs = []
    attempted = 0
    ranked = sorted(observations, key=lambda o: (o.after.numerator == 0,
                    -(o.before.numerator - o.after.numerator) / o.before.denominator, o.id))
    # Repeated hits constrain the same registered stats simultaneously, even
    # when their runtime fields, boosts or moves differ. Keep axis sets small.
    groups: dict[str, list[DamageObservation]] = {}
    for observation in ranked:
        signature = json.dumps({name: sorted(axes) for name, axes in
                                sorted(relevant_axes([observation]).items())}, sort_keys=True)
        groups.setdefault(signature, []).append(observation)
    candidates = (list(groups.values()) + [group[:2] for group in groups.values() if len(group) > 2]
                  + [[sample] for group in groups.values() if len(group) > 1 for sample in group])
    for samples in candidates:
        axes = relevant_axes(samples)
        local = {key: declarations[key] for key in axes}
        key = json.dumps([sample.model_dump(mode="json") for sample in samples], sort_keys=True) + json.dumps({name: {
            "species": value.species, "nature": value.nature, "stat_points": value.stat_points,
        } for name, value in sorted(local.items())}, sort_keys=True)
        cached = budget.factor_checkpoints.get(key)
        if cached is not None:
            proofs.append(cached)
            continue
        # Preserve budget for later games; before-game checkpoints reuse prior proofs.
        if phase != "after_game" or attempted >= 1:
            continue
        estimate = space_bound(local, axes, rules)
        remaining = budget.options.max_projections - budget.projections
        local_limit = min(100000, budget.options.max_assignments * 4)
        # Reserve some time/projections for the full joint search. The upper bound
        # may overestimate equivalent natures or legal budgets; skipping is sound.
        if (estimate > local_limit or estimate * len(samples) > max(0, remaining - 256)
                or time.monotonic() >= budget.deadline):
            continue
        attempted += 1
        proof = enumerate_constraints(game=game, phase=phase, declarations=local,
            observations=samples, unsupported=(), rules=rules, budget=budget, assignment_limit=local_limit)
        if proof.coverage == "exhaustive":
            budget.factor_checkpoints[key] = proof
            proofs.append(proof)
        if proof.outcome == "conflict":
            break
    return proofs
