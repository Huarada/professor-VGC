"""Certified local constraints supply sound outer bounds, never global joint samples."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Literal

from src.domain.bo3_inference.evidence_index import SeriesEvidenceIndex
from src.domain.bo3_inference.models import (
    DamageObservation,
    DeclaredSet,
    InferenceCheckpoint,
    RuleProfile,
    StatName,
)
from src.services.bo3_inference.boundary_search import boundary_bounds
from src.services.bo3_inference.evidence_retrieval import damage_factors
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
    """Complete affordable factors first; retain only certified partial exclusions."""
    proofs = []
    attempted = 0
    started = budget.factor_phase_starts.setdefault(game, budget.projections)
    phase_limit = max(256, budget.options.max_projections // budget.game_count)
    now = time.monotonic()
    deadline = budget.factor_deadlines.setdefault(game, now + (budget.deadline - now) / max(1, budget.game_count - game + 1))
    index = budget.evidence_index
    if index is None:
        index = SeriesEvidenceIndex(scope="local-search", pokemon_ids=declarations)
        index.append(observations)
    candidates = damage_factors(index=index, observations=observations, game=game)
    # Cross-game factors can narrow marginals that each hit alone leaves unknown.
    # Affordability/deadline checks still apply; incomplete factors retain only fully proven endpoint exclusions.
    primary_pairs: set[str] = set()
    ranked_candidates = []
    for samples in candidates:
        games = sorted({sample.game for sample in samples})
        signature = json.dumps([games, {key: sorted(axes) for key, axes in sorted(relevant_axes(samples).items())}], sort_keys=True)
        if len(games) > 1 and len(samples) == 2:
            priority = 0 if signature not in primary_pairs else 3
            primary_pairs.add(signature)
        else:
            priority = 1 if space_bound(declarations, relevant_axes(samples), rules) * len(samples) <= 48000 else 2
        ranked_candidates.append((priority, samples))
    candidates = [samples for _, samples in sorted(ranked_candidates, key=lambda entry: entry[0])]
    seen: set[str] = set()
    for samples in candidates:
        axes = relevant_axes(samples)
        local = {key: declarations[key] for key in axes}
        key = json.dumps([sample.model_dump(mode="json") for sample in samples], sort_keys=True) + json.dumps({name: {
            "species": value.species, "nature": value.nature, "stat_points": value.stat_points,
        } for name, value in sorted(local.items())}, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        cached = budget.factor_checkpoints.get(key)
        if cached is not None and (phase != "after_game" or cached.coverage == "exhaustive" or cached.marginal_bounds_complete):
            proofs.append(cached)
            continue
        # Preserve budget for later games; before-game checkpoints reuse prior proofs.
        if phase != "after_game":
            continue
        estimate = space_bound(local, axes, rules)
        remaining = budget.options.max_projections - budget.projections
        local_limit = min(100000, budget.options.max_assignments * 4)
        phase_remaining = phase_limit - (budget.projections - started)
        if (4096 < estimate <= local_limit and all(entry.nature for entry in local.values())
                and phase_remaining >= 256 and time.monotonic() < deadline):
            proof = boundary_bounds(game=game, phase=phase, declarations=local, observations=samples,
                rules=rules, budget=budget, projection_limit=min(local_limit, phase_remaining), deadline=deadline, previous=cached)
            if proof is not None:
                budget.factor_checkpoints[key] = proof
                proofs.append(proof)
                if proof.outcome == "conflict":
                    break
            elif cached is not None:
                proofs.append(cached)
            continue
        if cached is not None:
            proofs.append(cached)
            continue
        if attempted >= 1:
            continue
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
