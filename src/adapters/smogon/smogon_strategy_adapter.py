"""Smogon strategy/archetype knowledge adapter.

Derives macro-strategy knowledge (common sets, teammates, archetypes) from the
Chaos usage data, sourced through the shared
:class:`~src.adapters.chaos.chaos_repository.ChaosRepository`. When a species is
absent from the newest regulation, it walks the regulation-fallback chain (same
game only) so strategies still populate for a brand-new regulation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.adapters.chaos.chaos_repository import ChaosRepository, ChaosRepositoryLike
from src.adapters.smogon.archetype_signals import infer_archetypes
from src.domain.exceptions import ConfigurationError
from src.domain.models import SmogonStrategy


class ChaosStrategyAdapter:
    """Concrete :class:`~src.domain.interfaces.StrategyKnowledgeProvider`,
    over a directory (or single file) of Chaos data, or over any other
    ``ChaosRepositoryLike`` source passed in as ``repository``."""

    def __init__(
        self,
        path: str | Path | None = None,
        top_n: int = 4,
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
            raise ConfigurationError("ChaosStrategyAdapter requires either `path` or `repository`")

    def get_strategy(
        self, species: str, *, metagame: str | None = None, question: str | None = None
    ) -> SmogonStrategy:
        """Return derived strategy knowledge, walking regulation fallback.

        `question` is accepted (part of the `StrategyKnowledgeProvider`
        port) but unused: Chaos usage data has no free-text passages to
        choose between, only structured counts."""
        meta = self._repo.resolve_metagame(metagame)
        resolved = self._repo.resolve_mon(meta, species)
        if resolved is None:
            return SmogonStrategy(
                species=species,
                overview=(
                    "No usage data available for this species in the loaded Chaos "
                    "files (current regulation or up to 3 previous regulations of the "
                    "same game). Add a Chaos dump that covers it — see DATA.md."
                ),
            )
        mon, source = resolved
        return self._build(species, mon, source)

    def _build(self, species: str, mon: dict[str, Any], source: str) -> SmogonStrategy:
        top_moves = sorted(mon.get("Moves", {}).items(), key=lambda kv: kv[1], reverse=True)
        move_names = [name for name, _ in top_moves[: self._top_n + 2] if name]
        items = sorted(mon.get("Items", {}).items(), key=lambda kv: kv[1], reverse=True)
        abilities = sorted(mon.get("Abilities", {}).items(), key=lambda kv: kv[1], reverse=True)
        teammates = sorted(mon.get("Teammates", {}).items(), key=lambda kv: kv[1], reverse=True)
        common_teammates = [name for name, _ in teammates[: self._top_n] if name]

        # Scans both the top moves AND the top abilities for archetype
        # signals — a trapping core (Perish Trap) is just as often built
        # around an ABILITY (Shadow Tag, Arena Trap, Magnet Pull) as around
        # Perish Song itself; a move-only scan misses it entirely.
        archetypes = infer_archetypes(move_names, [a for a, _ in abilities[:3]])
        ability = abilities[0][0] if abilities else "?"
        item = items[0][0] if items else "?"
        move_str = ", ".join(move_names[:4]) if move_names else "?"
        overview = (
            f"[{source}] {species} most often runs "
            f"{', '.join(move_names[:3]) if move_names else 'no notable moves'}. "
            f"Frequently paired with "
            f"{', '.join(common_teammates[:3]) if common_teammates else 'no notable teammates'}."
        )
        return SmogonStrategy(
            species=species,
            overview=overview,
            common_sets=[f"{ability} @ {item} — {move_str}"],
            common_teammates=common_teammates,
            archetypes=archetypes,
        )
