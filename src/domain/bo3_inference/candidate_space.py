"""Legal profile families and joint enumeration without assuming unmeasured stats."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from src.domain.bo3_inference.models import (
    CandidateProfile,
    DeclaredSet,
    RuleProfile,
    StatName,
)


def profiles(
    pokemon_id: str, declaration: DeclaredSet, axes: set[StatName], rules: RuleProfile,
) -> Iterator[CandidateProfile]:
    """Group natures only by effects on measured axes, retaining their identities."""
    groups: dict[tuple[int, ...], list[str]] = {}
    ordered = sorted(axes)
    for nature in rules.natures:
        if declaration.nature and nature.name.casefold() != declaration.nature.casefold():
            continue
        effects = tuple(int(nature.plus == stat) - int(nature.minus == stat) for stat in ordered)
        groups.setdefault(effects, []).append(nature.name)
    free = [stat for stat in ordered if stat not in declaration.stat_points]

    def allocate(index: int, remaining: int, points: dict[StatName, int]) -> Iterator[dict[StatName, int]]:
        if index == len(free):
            yield dict(points)
            return
        stat = free[index]
        for value in range(min(rules.stat_cap, remaining) + 1):
            points[stat] = value
            yield from allocate(index + 1, remaining - value, points)
        points.pop(stat, None)

    for names in groups.values():
        for points in allocate(0, rules.total_budget - sum(declaration.stat_points.values()), dict(declaration.stat_points)):
            yield CandidateProfile(pokemon_id=pokemon_id, species=declaration.species,
                                   points=points, natures=tuple(names))


def joint_assignments(
    declarations: Mapping[str, DeclaredSet], axes: Mapping[str, set[StatName]], rules: RuleProfile,
) -> Iterator[dict[str, CandidateProfile]]:
    """Lazy product; never replace correlated solutions with independent marginals."""
    keys = sorted(axes)

    def visit(index: int, assigned: dict[str, CandidateProfile]) -> Iterator[dict[str, CandidateProfile]]:
        if index == len(keys):
            yield dict(assigned)
            return
        key = keys[index]
        for candidate in profiles(key, declarations[key], axes[key], rules):
            assigned[key] = candidate
            yield from visit(index + 1, assigned)
        assigned.pop(key, None)

    yield from visit(0, {})
