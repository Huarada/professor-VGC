"""Chaos statistics adapter (probabilistic metagame feed).

Builds the compact metagame context from Smogon *Chaos* usage stats, sourced
through a :class:`~src.adapters.chaos.chaos_repository.ChaosRepository` so it can:

* use the IDEAL rating tier (highest cutoff, e.g. ``-1760``) as the aspirational
  suggestion, while also surfacing the CURRENT ladder-tier bracket for the match;
* fall back to older regulations of the same game when a species is missing from
  the newest regulation.

Implements :class:`~src.domain.interfaces.MetaStatsProvider`. Only Chaos on-disk
details live in the repository/adapter; the rest of the system consumes the
typed :class:`~src.domain.models.MetaContext`.

Chaos stores EVs divided by 8, e.g. ``"Bold:32/0/32/2/0/0"``; multiplying each
component by 8 recovers real 0-252 EVs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.adapters.chaos.chaos_repository import ChaosRepository, ChaosRepositoryLike
from src.domain.exceptions import ConfigurationError
from src.domain.models import MetaContext, PokemonMetaSummary, StatSpread

_EV_MULTIPLIER = 8
_STAT_LABELS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")
_STAT_FIELDS = ("hp", "atk", "def", "spa", "spd", "spe")


class ChaosAdapter:
    """MetaStatsProvider over a directory (or single file) of Chaos data, or
    over any other ``ChaosRepositoryLike`` source (e.g. Firestore — see
    ``firestore_chaos_repository.py``) passed in as ``repository``.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        top_n: int = 3,
        reg_fallback_depth: int = 3,
        *,
        repository: ChaosRepositoryLike | None = None,
    ) -> None:
        self._top_n = max(1, int(top_n))
        if repository is not None:
            self._repo: ChaosRepositoryLike = repository
        elif path is not None:
            self._repo = ChaosRepository(path, reg_fallback_depth=reg_fallback_depth)
        else:
            raise ConfigurationError("ChaosAdapter requires either `path` or `repository`")
        # Back-compat attribute used by some callers/tests.
        self.metagame = self._repo.default_metagame()

    # -- extraction helpers --------------------------------------------- #

    @staticmethod
    def _parse_spread(spread_str: str) -> tuple[str, list[int]] | None:
        """Chaos stores EVs divided by 8, e.g. "Bold:32/0/32/2/0/0" — split
        into (nature, real 0-252 EV values) or None if the string is
        malformed (defensive: Chaos dumps are external data, not guaranteed)."""
        try:
            nature, evs = spread_str.split(":")
            values = [int(component) * _EV_MULTIPLIER for component in evs.split("/")]
        except (ValueError, AttributeError):
            return None
        if len(values) != len(_STAT_LABELS):
            return None
        return nature, values

    def _convert_ev_divider(self, spread_str: str) -> str:
        parsed = self._parse_spread(spread_str)
        if parsed is None:
            return spread_str
        nature, values = parsed
        body = " / ".join(f"{v} {label}" for v, label in zip(values, _STAT_LABELS))
        return f"{nature} ({body})"

    def _structured_top_spread(
        self, spreads: list[tuple[str, Any]]
    ) -> tuple[str | None, StatSpread | None]:
        """The single most-used spread, machine-readable, for calc back-fill."""
        for spread_str, _weight in spreads:
            parsed = self._parse_spread(spread_str)
            if parsed is None:
                continue
            nature, values = parsed
            return nature, StatSpread(**dict(zip(_STAT_FIELDS, values)))
        return None, None

    @staticmethod
    def _top_items(mapping: dict[str, Any], count: int, weight: float) -> dict[str, float]:
        ordered = sorted(mapping.items(), key=lambda kv: kv[1], reverse=True)[:count]
        return {key: round(float(value) / weight, 3) for key, value in ordered if key}

    def _summarize(
        self, mon_data: dict[str, Any], source: str, count: int
    ) -> PokemonMetaSummary:
        raw_count = float(mon_data.get("Raw count", 1)) or 1.0
        moves = sorted(
            mon_data.get("Moves", {}).items(), key=lambda kv: kv[1], reverse=True
        )[: count + 2]
        spreads = sorted(
            mon_data.get("Spreads", {}).items(), key=lambda kv: kv[1], reverse=True
        )[:count]
        counters = sorted(
            mon_data.get("Checks and Counters", {}).items(),
            key=lambda kv: kv[1].get("p", 0),
            reverse=True,
        )[:count]
        top_spread_nature, top_spread_evs = self._structured_top_spread(spreads)
        return PokemonMetaSummary(
            top_abilities=self._top_items(mon_data.get("Abilities", {}), count, raw_count),
            top_items=self._top_items(mon_data.get("Items", {}), count, raw_count),
            top_moves={k: round(float(v) / raw_count, 3) for k, v in moves if k},
            top_spreads=[self._convert_ev_divider(sp) for sp, _ in spreads],
            top_spread_nature=top_spread_nature,
            top_spread_evs=top_spread_evs,
            threats_winrate={n: round(float(p.get("p", 0.0)), 2) for n, p in counters},
            source=source,
        )

    # -- public API ------------------------------------------------------ #

    def get_pokemon_summary(
        self, pokemon_name: str, top_n: int | None = None, *, metagame: str | None = None
    ) -> PokemonMetaSummary:
        """Top-N summary from the ideal tier, with regulation fallback."""
        count = self._top_n if top_n is None else max(1, int(top_n))
        meta = self._repo.resolve_metagame(metagame)
        resolved = self._repo.resolve_mon(meta, pokemon_name)
        if resolved is None:
            return PokemonMetaSummary()
        mon_data, source = resolved
        return self._summarize(mon_data, source, count)

    def _summary_from_file(
        self, file: Any, species: str, count: int
    ) -> PokemonMetaSummary | None:
        mon_data = self._repo.mon_data(file, species)
        if not mon_data:
            return None
        return self._summarize(mon_data, f"{file.metagame}@{file.cutoff}", count)

    def build_match_context(
        self,
        species: Sequence[str],
        *,
        metagame: str | None = None,
        rating: int | None = None,
    ) -> MetaContext:
        """Ideal-tier context (with reg fallback) plus the current-tier bracket."""
        meta = self._repo.resolve_metagame(metagame)
        ideal_file = self._repo.ideal_file(meta)
        current_file = self._repo.current_file(meta, rating)

        pokemon_stats = {name: self.get_pokemon_summary(name, metagame=meta) for name in species}

        current_stats: dict[str, PokemonMetaSummary] = {}
        if current_file is not None and (
            ideal_file is None
            # Compare logical identity (metagame, cutoff), not a storage
            # handle like a local Path — the latter doesn't exist at all on
            # a Firestore-backed tier (FirestoreChaosFile.doc_id instead),
            # and comparing the tier's own coordinates is what "is this
            # actually a different tier" means regardless of backend.
            or (current_file.metagame, current_file.cutoff)
            != (ideal_file.metagame, ideal_file.cutoff)
        ):
            for name in species:
                summary = self._summary_from_file(current_file, name, self._top_n)
                if summary is not None:
                    current_stats[name] = summary

        note = self._rating_note(ideal_file, current_file, rating)
        return MetaContext(
            metagame=meta,
            pokemon_stats=pokemon_stats,
            current_tier_stats=current_stats,
            rating_note=note,
        )

    @staticmethod
    def _rating_note(
        ideal_file: Any, current_file: Any, rating: int | None
    ) -> str:
        parts: list[str] = []
        if ideal_file is not None:
            parts.append(f"ideal tier: {ideal_file.metagame}@{ideal_file.cutoff}")
        if rating is not None:
            parts.append(f"match rating: {rating}")
        if current_file is not None:
            parts.append(f"current tier: {current_file.metagame}@{current_file.cutoff}")
        return "; ".join(parts)
