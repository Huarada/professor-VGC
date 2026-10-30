"""Solve independent profile components without enumerating their Cartesian product."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import islice, product
from math import prod
from typing import Literal

from src.domain.bo3_inference.models import (
    DamageObservation,
    DeclaredSet,
    InferenceCheckpoint,
    RuleProfile,
)
from src.domain.bo3_inference.session import summarize
from src.services.bo3_inference.solver import ProjectionBudget, relevant_axes, solve


def solve_components(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    components: Sequence[Sequence[DamageObservation]], unsupported: Sequence[str], rules: RuleProfile,
    budget: ProjectionBudget, assignment_limit: int,
) -> InferenceCheckpoint:
    """Every complete component is exact; unfinished ones retain unresolved regions."""
    results = []
    remaining = assignment_limit
    # Small components yield useful proofs even when later components hit a limit.
    from src.services.bo3_inference.factor_bounds import space_bound

    ordered = sorted(components, key=lambda samples: space_bound(declarations, relevant_axes(samples), rules))
    for samples in ordered:
        keys = relevant_axes(samples)
        local = {key: entry for key, entry in declarations.items() if key in keys}
        checkpoint = solve(game=game, phase=phase, declarations=local, observations=samples,
                           unsupported=(), rules=rules, budget=budget, assignment_limit=remaining)
        remaining -= checkpoint.evaluated_assignments
        results.append(checkpoint)
    by_id = {summary.pokemon_id: summary for result in results for summary in result.summaries}
    summaries = tuple(by_id[key] if key in by_id else summarize(key, entry, rules)
                      for key, entry in declarations.items())
    conflict = any(result.outcome == "conflict" for result in results)
    complete = all(result.coverage == "exhaustive" for result in results)
    count = prod(result.compatible_families or 0 for result in results) if complete else None
    coverage: Literal["exhaustive", "bounded", "unsupported"] = "exhaustive" if complete or conflict else "bounded"
    outcome: Literal["not_informative", "ambiguous", "identified_within_model", "conflict"] = "ambiguous"
    if conflict:
        outcome, count = "conflict", 0
    elif complete and all(result.outcome == "identified_within_model" for result in results):
        outcome = "identified_within_model"
    elif all(result.outcome == "not_informative" for result in results):
        outcome = "not_informative"
    # Only combine whole compatible assignments from disjoint components, never marginals.
    examples = tuple({key: value for part in parts for key, value in part.items()}
                     for parts in islice(product(*(result.joint_examples for result in results)), 12)) if not conflict else ()
    return InferenceCheckpoint(
        id=f"g{game}:{phase}", game=game, phase=phase, perspective=budget.options.perspective,
        outcome=outcome, coverage=coverage, evaluated_assignments=assignment_limit - remaining,
        compatible_families=count, summaries=summaries, joint_examples=examples,
        observation_ids=tuple(sample.id for sample in sorted(
            (sample for samples in components for sample in samples),
            key=lambda sample: (sample.game, sample.event_index, sample.id))),
        unsupported=tuple(sorted(set(unsupported).union(*(result.unsupported for result in results)))),
        local_constraints=tuple(dict.fromkeys(key for result in results for key in result.local_constraints)),
        local_evaluated_assignments=sum(result.local_evaluated_assignments for result in results),
        search_components=len(results),
    )
