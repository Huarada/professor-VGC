"""Composite strategy provider: official Smogon analyses, Chaos as fallback.

Tries the official @pkmn/smogon analyses first (natural-language strategy). If
those are unavailable (no network, missing entry), it falls back to the local
Chaos-derived strategy so the pipeline always returns something useful.
"""

from __future__ import annotations

from src.domain.exceptions import StrategyKnowledgeError
from src.domain.interfaces import StrategyKnowledgeProvider
from src.domain.models import SmogonStrategy


class CompositeStrategyProvider:
    """Concrete :class:`~src.domain.interfaces.StrategyKnowledgeProvider`."""

    def __init__(
        self,
        primary: StrategyKnowledgeProvider,
        fallback: StrategyKnowledgeProvider,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_strategy(
        self, species: str, *, metagame: str | None = None, question: str | None = None
    ) -> SmogonStrategy:
        try:
            strategy = self._primary.get_strategy(
                species, metagame=metagame, question=question
            )
            if strategy.overview and "No usage data" not in strategy.overview:
                return strategy
        except StrategyKnowledgeError:
            pass
        return self._fallback.get_strategy(species, metagame=metagame, question=question)
