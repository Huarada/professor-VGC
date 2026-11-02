"""Exact, analysis-local retrieval of BO3 damage observations and dependencies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.models import (
    DamageObservation,
    EvidenceQuery,
    EvidenceRetrieval,
    StatName,
)
from src.domain.exceptions import SeriesValidationError
from src.domain.names import to_id


class SeriesEvidenceIndex:
    """Own evidence for exactly one analysis, never keyed by species or Dex ID.

    Each identity is an existing registered participant/slot ID. A new instance
    is required for every analysis and for each unregistered game's context.
    Snapshots returned by retrieval cannot mutate indexed event-time state.
    """

    def __init__(self, *, scope: str, pokemon_ids: Iterable[str]) -> None:
        self.scope = scope
        self._pokemon_ids = frozenset(pokemon_ids)
        self._observations: dict[str, DamageObservation] = {}
        self._pokemon: dict[str, set[str]] = defaultdict(set)
        self._games: dict[int, set[str]] = defaultdict(set)
        self._axes: dict[tuple[str, StatName], set[str]] = defaultdict(set)
        self._moves: dict[str, set[str]] = defaultdict(set)
        self._retrievals: dict[EvidenceQuery, EvidenceRetrieval] = {}

    def append(self, observations: Iterable[DamageObservation]) -> None:
        """Atomically append validated evidence; exact duplicate IDs are idempotent."""
        pending = dict(self._observations)
        for sample in observations:
            if sample.attacker_id not in self._pokemon_ids or sample.defender_id not in self._pokemon_ids:
                raise SeriesValidationError("Damage evidence refers to a Pokemon outside this series roster.")
            if not 1 <= sample.game <= 3 or sample.event_index < 0:
                raise SeriesValidationError("Damage evidence has an invalid BO3 position.")
            old = pending.get(sample.id)
            if old is not None and old != sample:
                raise SeriesValidationError("Conflicting damage observations share an evidence ID.")
            pending[sample.id] = sample
        for key, sample in pending.items():
            if key in self._observations:
                continue
            self._observations[key] = sample.model_copy(deep=True)
            self._games[sample.game].add(key)
            for pokemon in {sample.attacker_id, sample.defender_id}:
                self._pokemon[pokemon].add(key)
            for pokemon, axes in relevant_axes([sample]).items():
                for stat in axes:
                    self._axes[pokemon, stat].add(key)
            self._moves[to_id(sample.request.move)].add(key)

    def query(self, query: EvidenceQuery) -> tuple[DamageObservation, ...]:
        """Retrieve exact historical observations, including all runtime modifiers."""
        ids = set().union(*(values for game, values in self._games.items() if game <= query.through_game))
        if query.game is not None:
            ids.intersection_update(self._games.get(query.game, ()))
        if query.pokemon_id is not None:
            ids.intersection_update(self._pokemon.get(query.pokemon_id, ()))
        if query.stat is not None:
            axes = (self._axes.get((query.pokemon_id, query.stat), set()) if query.pokemon_id is not None else
                    set().union(*(values for (_, stat), values in self._axes.items() if stat == query.stat)))
            ids.intersection_update(axes)
        if query.move is not None:
            ids.intersection_update(self._moves.get(to_id(query.move), ()))
        samples = sorted((self._observations[key] for key in ids), key=lambda sample: (sample.game, sample.event_index, sample.id))
        found = tuple(sample for sample in samples
                      if (query.through_event is None or sample.game < query.through_game or sample.event_index <= query.through_event)
                      and (query.role == "any" or query.pokemon_id is None or
                           getattr(sample, f"{query.role}_id") == query.pokemon_id))
        self._retrievals[query] = EvidenceRetrieval(scope=self.scope, query=query, observation_ids=tuple(sample.id for sample in found))
        return tuple(sample.model_copy(deep=True) for sample in found)

    @property
    def retrievals(self) -> tuple[EvidenceRetrieval, ...]:
        """Return the most recent exact query results, without storing model prose."""
        return tuple(self._retrievals.values())
