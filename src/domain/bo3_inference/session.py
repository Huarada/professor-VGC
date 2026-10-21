"""Request-scoped inference state and immutable checkpoint summaries."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.bo3_inference.models import (
    STATS,
    CandidateProfile,
    CandidateSummary,
    DeclaredSet,
    InferenceCheckpoint,
    PointRange,
    RuleProfile,
)


def summarize(
    pokemon_id: str, declaration: DeclaredSet, rules: RuleProfile,
    candidates: list[CandidateProfile] | None = None,
) -> CandidateSummary:
    """Ranges cover ALL legal completions, not just representative zero-filled sets."""
    names = tuple(n.name for n in rules.natures
                  if not declaration.nature or n.name.casefold() == declaration.nature.casefold())
    families = candidates or [CandidateProfile(pokemon_id=pokemon_id, species=declaration.species,
                                             points=declaration.stat_points, natures=names)]
    ranges = {}
    for stat in STATS:
        lower = [family.points.get(stat, 0) for family in families]
        upper = [family.points.get(stat, min(rules.stat_cap, rules.total_budget - sum(family.points.values())))
                 for family in families]
        ranges[stat] = PointRange(minimum=min(lower), maximum=max(upper))
    return CandidateSummary(pokemon_id=pokemon_id, species=declaration.species, stat_points=ranges,
                            natures=tuple(sorted({n for family in families for n in family.natures})),
                            basis="compatible_assignments" if candidates else "declarations",
                            declared_stat_points=tuple(declaration.stat_points), nature_declared=declaration.nature is not None,
                            field_sources=dict(declaration.field_sources))


def retain_known_bounds(current: InferenceCheckpoint, previous: InferenceCheckpoint) -> InferenceCheckpoint:
    """New evidence/budgets cannot erase sound prior outer bounds or correlations."""
    if current.coverage == "exhaustive" or current.outcome == "conflict":
        return current
    earlier = {summary.pokemon_id: summary for summary in previous.summaries}
    summaries = []
    for summary in current.summaries:
        old = earlier.get(summary.pokemon_id)
        if old is None:
            summaries.append(summary)
            continue
        ranges = {stat: PointRange(minimum=max(bound.minimum, old.stat_points[stat].minimum),
                                   maximum=min(bound.maximum, old.stat_points[stat].maximum))
                  for stat, bound in summary.stat_points.items()}
        natures = tuple(sorted(set(summary.natures).intersection(old.natures)))
        if any(bound.minimum > bound.maximum for bound in ranges.values()) or not natures:
            return current.model_copy(update={"outcome": "conflict", "coverage": "exhaustive",
                                              "compatible_families": 0, "summaries": previous.summaries,
                                              "unsupported": (*current.unsupported, "New declarations contradict prior compatible bounds")})
        summaries.append(summary.model_copy(update={"stat_points": ranges, "natures": natures}))
    return current.model_copy(update={"summaries": tuple(summaries)})


class SeriesInferenceSession:
    """Append-only checkpoints; an explicit declaration revision requires a new run."""

    def __init__(self, *, rules: RuleProfile) -> None:
        self.rules = rules
        self.checkpoints: list[InferenceCheckpoint] = []

    def append(self, checkpoint: InferenceCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    def last_valid(self, declarations: Mapping[str, DeclaredSet]) -> tuple[CandidateSummary, ...]:
        for checkpoint in reversed(self.checkpoints):
            if checkpoint.outcome != "conflict" and checkpoint.summaries:
                return checkpoint.summaries
        return tuple(summarize(key, declaration, self.rules) for key, declaration in declarations.items())
