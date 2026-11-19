"""Bounded, honest provenance for BO3 stat-point reductions."""

from collections.abc import Sequence

from src.domain.bo3_inference.models import (
    CandidateSummary,
    DamageCitation,
    DamageObservation,
    HPReading,
    InferenceCheckpoint,
    StatName,
)


def cite_observation(observation: DamageObservation, axes: dict[str, set[StatName]]) -> DamageCitation:
    """Keep only the public fields needed to identify a modeled hit."""
    return DamageCitation(id=observation.id, game=observation.game, event_index=observation.event_index,
        attacker_id=observation.attacker_id, defender_id=observation.defender_id,
        attacker_species=observation.request.attacker.species,
        defender_species=observation.request.defender.species, move=observation.request.move,
        before=observation.before, after=observation.after,
        dependent_axes={key: tuple(sorted(stats)) for key, stats in axes.items() if stats})


def _hp(reading: HPReading) -> str:
    return f"{reading.numerator}%" if reading.denominator == 100 else f"{reading.numerator}/{reading.denominator} HP"


def describe_hit(citation: DamageCitation) -> str:
    """A percent display is a logged HP reading, never exact damage."""
    return (f"game {citation.game} event {citation.event_index}: "
            f"{citation.move} by {citation.attacker_species} into {citation.defender_species}, "
            f"{_hp(citation.before)} -> {_hp(citation.after)}")


def bound_evidence(
    checkpoint: InferenceCheckpoint, summary: CandidateSummary, stat: StatName,
    citations: Sequence[DamageCitation], *, limit: int = 4,
) -> tuple[str, tuple[DamageCitation, ...]]:
    """Separate leave-one-out dependencies from merely related certified hits.

    A relevant hit can participate in a joint constraint without being essential
    alone. Never call that hit a standalone proof of the displayed interval.
    """
    if checkpoint.outcome == "conflict":
        return "", ()
    indexed = {citation.id: citation for citation in citations if citation.game <= checkpoint.game}
    essential = summary.single_observation_bounds.get(stat, ())
    selected = [indexed[identifier] for identifier in essential if identifier in indexed
                and identifier in checkpoint.observation_ids]
    if selected:
        return "Leave-one-out dependencies recorded during the search; later hits may add redundancy", tuple(selected[:limit])
    certified = checkpoint.local_constraints or (checkpoint.observation_ids if checkpoint.coverage == "exhaustive" else ())
    selected = [indexed[identifier] for identifier in certified
                if identifier in indexed and identifier in checkpoint.observation_ids
                and stat in indexed[identifier].dependent_axes.get(summary.pokemon_id, ())]
    if selected:
        # Examples, not an individually sufficient or exhaustive proof core.
        chosen = selected[:limit - 1] + selected[-1:] if len(selected) > limit else selected
        return "Related certified hits; they may constrain the interval jointly", tuple(chosen)
    selected = [indexed[identifier] for identifier in checkpoint.partial_local_constraints
                if identifier in indexed and identifier in checkpoint.observation_ids
                and stat in indexed[identifier].dependent_axes.get(summary.pokemon_id, ())]
    if selected:
        return "Related budget-limited hits with certified endpoint exclusions", tuple(selected[:limit])
    return "", ()
