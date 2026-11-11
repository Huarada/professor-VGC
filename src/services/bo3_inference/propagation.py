"""Exact per-hit feasibility tables intersected by arc consistency (ADR-068).

A value is removed only when one hit's complete table (or the 66-point budget)
has no supporting combination left, so every bound is a sound outer bound.
Hits over identical axes are intersected cell by cell first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Literal

from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.models import (
    DamageObservation,
    DeclaredSet,
    InferenceCheckpoint,
    PointRange,
    ProjectionRequest,
    RuleProfile,
    StatName,
    TableAxis,
    TableRequest,
)
from src.domain.bo3_inference.session import summarize
from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CandidateTablePort
from src.services.bo3_inference.solver import ProjectionBudget

Var = tuple[str, StatName]
SIDE = 33
Table = tuple[tuple[Var, ...], int]


@lru_cache(maxsize=None)
def coordinate_mask(size: int, position: int, value: int) -> int:
    """Cells of a size-axis table whose axis `position` equals `value`."""
    stride = SIDE ** (size - 1 - position)
    block = ((1 << stride) - 1) << (value * stride)
    mask = 0
    for repeat in range(SIDE ** position):
        mask |= block << (repeat * stride * SIDE)
    return mask


def _bits(mask: int) -> list[int]:
    return [value for value in range(SIDE) if mask >> value & 1]


def table_request(observation: DamageObservation, declarations: Mapping[str, DeclaredSet]) -> tuple[tuple[Var, ...], TableRequest] | None:
    """Undeclared measured axes of one hit; None when a table cannot represent it."""
    attacker, defender = observation.attacker_id, observation.defender_id
    # shortcut: tables need fixed natures; undeclared natures stay with the legacy search.
    if attacker == defender or declarations[attacker].nature is None or declarations[defender].nature is None:
        return None
    variables = tuple(sorted((key, stat) for key, stats in relevant_axes([observation]).items()
                             for stat in stats if stat not in declarations[key].stat_points))
    if not variables or len(variables) > 3:
        return None
    base = ProjectionRequest(calc=observation.request,
        attacker_points=dict(declarations[attacker].stat_points), defender_points=dict(declarations[defender].stat_points),
        attacker_nature=declarations[attacker].nature or "", defender_nature=declarations[defender].nature or "",
        before=observation.before, attacker_before=observation.attacker_before)
    axes = tuple(TableAxis(side="attacker" if key == attacker else "defender", stat=stat) for key, stat in variables)
    return variables, TableRequest(base=base, axes=axes, after=observation.after)


def propagate(tables: Sequence[Table], declarations: Mapping[str, DeclaredSet], rules: RuleProfile) -> dict[Var, int] | None:
    """Arc-consistent value masks per variable; None when the evidence conflicts."""
    merged: dict[tuple[Var, ...], int] = {}
    for variables, feasible in tables:
        merged[variables] = merged.get(variables, feasible) & feasible
    domains: dict[Var, int] = {}
    for variables in merged:
        for key, stat in variables:
            spare = rules.total_budget - sum(declarations[key].stat_points.values())
            domains[(key, stat)] = (1 << (min(rules.stat_cap, spare) + 1)) - 1 if spare >= 0 else 0
    owners: dict[str, list[Var]] = {}
    for var in domains:
        owners.setdefault(var[0], []).append(var)
    changed = True
    while changed:
        changed = False
        for variables, feasible in merged.items():
            allowed = feasible
            for position, var in enumerate(variables):
                union = 0
                for value in _bits(domains[var]):
                    union |= coordinate_mask(len(variables), position, value)
                allowed &= union
            if not allowed:
                return None
            for position, var in enumerate(variables):
                support = sum(1 << value for value in _bits(domains[var])
                              if allowed & coordinate_mask(len(variables), position, value))
                if support != domains[var]:
                    domains[var] = support
                    changed = True
        for key, owned in owners.items():
            if any(not domains[var] for var in owned):
                return None
            for var in owned:
                floor = sum(declarations[key].stat_points.values()) + sum(
                    _bits(domains[other])[0] for other in owned if other != var)
                capped = domains[var] & ((1 << max(0, rules.total_budget - floor + 1)) - 1)
                if capped != domains[var]:
                    domains[var] = capped
                    changed = True
    return None if any(not mask for mask in domains.values()) else domains


def propagated_bounds(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    observations: Sequence[DamageObservation], rules: RuleProfile, budget: ProjectionBudget,
) -> InferenceCheckpoint | None:
    """Sound outer bounds, interior exclusions and single-observation dependencies."""
    port = budget.port
    if not isinstance(port, CandidateTablePort):
        return None
    tables: dict[str, Table] = {}
    for observation in observations:
        built = table_request(observation, declarations)
        if built is None:
            continue
        variables, request = built
        key = request.model_dump_json()
        if key not in budget.tables:
            if budget.expired():
                budget.factor_limit_reasons.setdefault(game, set()).add(
                    "Per-hit table time budget reached; remaining hits did not narrow bounds")
                continue
            budget.report_batch([request.base])
            try:
                cells = port.project_table(request).cells
            except CalcEngineError as exc:
                budget.factor_limit_reasons.setdefault(game, set()).add(f"{observation.id}: {exc}")
                continue
            budget.completed_checks += len(cells)
            # Unsupported cells stay feasible: they can never exclude a value.
            budget.tables[key] = int(cells[::-1].replace("?", "1"), 2)  # bit i is cell i
        tables[observation.id] = (variables, budget.tables[key])
    if not tables:
        return None
    domains = propagate(list(tables.values()), declarations, rules)
    initial = {key: summarize(key, entry, rules) for key, entry in declarations.items()}
    dependent: dict[Var, list[str]] = {}
    if domains is not None:
        for identifier in tables:
            without = propagate([table for other, table in tables.items() if other != identifier], declarations, rules)
            for var, mask in domains.items():
                if without is None or without.get(var, -1) != mask:
                    dependent.setdefault(var, []).append(identifier)
    summaries = []
    for key, baseline in initial.items():
        points = dict(baseline.stat_points)
        excluded: dict[StatName, tuple[int, ...]] = {}
        single: dict[StatName, tuple[str, ...]] = {}
        for (owner, stat), mask in (domains or {}).items():
            if owner != key:
                continue
            values = _bits(mask)
            points[stat] = PointRange(minimum=values[0], maximum=values[-1])
            if holes := tuple(sorted(set(range(values[0], values[-1] + 1)) - set(values))):
                excluded[stat] = holes
            if dependent.get((owner, stat)):
                single[stat] = tuple(dependent[(owner, stat)])
        points = {stat: PointRange(minimum=bound.minimum, maximum=min(bound.maximum,
            rules.total_budget - sum(other.minimum for name, other in points.items() if name != stat)))
            for stat, bound in points.items()}
        excluded = {stat: tuple(value for value in holes if value <= points[stat].maximum)
                    for stat, holes in excluded.items()}
        summaries.append(baseline.model_copy(update={
            "stat_points": points, "excluded_points": {stat: holes for stat, holes in excluded.items() if holes},
            "single_observation_bounds": single,
            "basis": "necessary_local_bounds" if points != baseline.stat_points or excluded else baseline.basis}))
    return InferenceCheckpoint(id=f"g{game}:{phase}:propagation", game=game, phase=phase,
        perspective=budget.options.perspective, outcome="conflict" if domains is None else "ambiguous",
        coverage="bounded", observation_ids=tuple(tables), summaries=tuple(summaries),
        evaluated_assignments=sum(SIDE ** len(variables) for variables, _ in tables.values()),
        marginal_bounds_complete=True)
