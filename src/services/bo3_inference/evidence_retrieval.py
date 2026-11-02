"""Retrieve and plan bounded combinations of historical damage constraints."""

from collections.abc import Sequence
from itertools import combinations, islice, product

from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.evidence_index import SeriesEvidenceIndex
from src.domain.bo3_inference.models import DamageObservation, EvidenceQuery


def damage_factors(
    *, index: SeriesEvidenceIndex, observations: Sequence[DamageObservation], game: int,
) -> list[list[DamageObservation]]:
    """Compare multiple cross-game hits while bounding factor-plan growth.

    Index lookup uses registered identities and actual dependency axes, including
    alternate-stat moves. Each observation retains its original runtime request.
    Only IDs in the current search component are eligible; future evidence is not.
    """
    permitted = {sample.id for sample in observations}
    selected: dict[str, DamageObservation] = {}
    for pokemon, axes in sorted(relevant_axes(observations).items()):
        for stat in sorted(axes):
            for sample in index.query(EvidenceQuery(through_game=game, pokemon_id=pokemon, stat=stat)):
                if sample.id in permitted:
                    selected[sample.id] = sample
    ranked = sorted(selected.values(), key=lambda sample: (
        sample.after.numerator == 0, -(sample.before.numerator - sample.after.numerator) / sample.before.denominator,
        sample.game, sample.event_index, sample.id))
    groups: dict[tuple[tuple[str, tuple[str, ...]], ...], list[DamageObservation]] = {}
    for sample in ranked:
        signature = tuple((pokemon, tuple(sorted(axes))) for pokemon, axes in sorted(relevant_axes([sample]).items()))
        groups.setdefault(signature, []).append(sample)
    candidates: list[list[DamageObservation]] = []
    exploration: list[list[DamageObservation]] = []
    for group in groups.values():
        by_game: dict[int, list[DamageObservation]] = {}
        for sample in group:
            by_game.setdefault(sample.game, []).append(sample)
        primary = [by_game[number][0] for number in sorted(by_game)]
        candidates.extend(list(pair) for pair in combinations(primary, 2))
        # Up to four observations per game produce at most 48 cross-game pairs.
        representatives = [sample for number in sorted(by_game) for sample in by_game[number][:4]]
        exploration.extend(list(pair) for pair in islice(
            (pair for pair in combinations(representatives, 2) if pair[0].game != pair[1].game), 48))
        if len(by_game) == 3:
            exploration.extend(list(triple) for triple in product(*(by_game[number][:2] for number in sorted(by_game))))
        candidates.append(group)
        if len(group) > 2:
            candidates.append(group[:2])
        if len(group) > 1:
            candidates.extend([sample] for sample in group)
    # Equivalent factor order must reuse the same projection/proof cache key.
    return [sorted(samples, key=lambda sample: (sample.game, sample.event_index, sample.id)) for samples in candidates + exploration]
