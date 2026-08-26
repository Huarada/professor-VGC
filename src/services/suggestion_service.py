"""Team-improvement suggestions from official Smogon sets + usage stats.

Per the ADR: when the player asks for improvements, use ``smogon.stats`` to find
team synergies and ``smogon.sets`` (together with the Chaos file) to advise on
moveset/item/ability/EV changes, citing synergy in the current team + usage %.

Only builds this context when the user's question signals such intent, to keep
the network calls (and prompt size) targeted.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from src.domain.exceptions import StrategyKnowledgeError
from src.domain.models import PokemonMetaSummary

_INTENT_KEYWORDS = (
    "improve", "improvement", "suggest", "suggestion", "synergy", "adjust",
    "optimize", "better", "build", "moveset", "spread", "ev", "evs", "item",
    "ability", "tera", "team", "swap this",
    # pt-br
    "melhor", "melhoria", "melhorar", "sugest", "sinergia", "ajuste", "ajustar",
    "otimiz", "montar", "movimento", "conjunto", "equipe", "time",
)


class SmogonSuggestionSource(Protocol):
    """Minimal port for the official Smogon sets/stats needed for suggestions."""

    def get_sets(self, species: str, *, metagame: str | None = None) -> list[dict[str, Any]]:
        ...

    def get_stats(
        self, species: str, *, metagame: str | None = None
    ) -> PokemonMetaSummary:
        ...

    def get_teammates(
        self, species: str, *, metagame: str | None = None
    ) -> dict[str, float]:
        ...


def wants_suggestions(question: str) -> bool:
    """True if the question asks for team/set improvements."""
    q = (question or "").lower()
    return any(word in q for word in _INTENT_KEYWORDS)


def build_improvement_context(
    source: SmogonSuggestionSource,
    species: Sequence[str],
    metagame: str | None,
) -> dict[str, Any]:
    """Compact official sets + synergy stats for each requested species."""
    out: dict[str, Any] = {}
    for name in species:
        entry: dict[str, Any] = {}
        try:
            entry["official_sets"] = source.get_sets(name, metagame=metagame)[:3]
        except StrategyKnowledgeError:
            pass
        try:
            entry["teammates_usage"] = source.get_teammates(name, metagame=metagame)
        except StrategyKnowledgeError:
            pass
        try:
            stats = source.get_stats(name, metagame=metagame)
            entry["usage_stats"] = stats.model_dump()
        except StrategyKnowledgeError:
            pass
        if entry:
            out[name] = entry
    return out
