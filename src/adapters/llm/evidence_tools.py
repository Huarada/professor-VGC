"""Read-only tools over the deterministic ports, for the explanation agents.

The one implementation; ``adk_tools.py`` / ``langchain_tools.py`` only adapt
it. Every tool:

- returns ``{"ok": True, ...}`` or ``{"ok": False, "error": str}``, never raises;
- has no default parameter values (Gemini's function schema rejects them;
  ``""`` means unset);
- has a Google-style docstring (ADK builds the tool description from it);
- is bound to the analysis' regulation (ADR-035) and refuses illegal species.
"""

from __future__ import annotations

from typing import Any, Callable

from src.domain.exceptions import CalcEngineError, ChaosDataError, StrategyKnowledgeError
from src.domain.interfaces import (
    CalcEngineAdapter,
    MetaStatsProvider,
    RegulationCatalog,
    StrategyKnowledgeProvider,
)
from src.domain.models import CalcRequest, PokemonSet
from src.domain.regulation import RegulationScope

ToolFunction = Callable[..., dict[str, Any]]


class EvidenceTools:
    """The three on-demand deterministic lookups, bound to concrete ports."""

    def __init__(
        self,
        *,
        calc_engine: CalcEngineAdapter,
        meta_provider: MetaStatsProvider,
        strategy_provider: StrategyKnowledgeProvider,
        default_gen: int = 9,
        scope: RegulationScope | None = None,
        regulation_catalog: RegulationCatalog | None = None,
    ) -> None:
        self._calc = calc_engine
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._gen = default_gen
        self._scope = scope or RegulationScope()
        self._catalog = regulation_catalog

    def _not_legal(self, species: list[str]) -> dict[str, Any] | None:
        """An error payload when any species is not legal in the bound
        regulation (None when all are legal, or legality is unknown)."""
        regulation = self._scope.current
        if regulation is None or self._catalog is None:
            return None
        roster = self._catalog.roster(regulation.format_id)
        if roster.empty:
            return None
        outside = [name for name in species if not roster.allows(name)]
        if not outside:
            return None
        return {
            "ok": False,
            "error": (
                f"{', '.join(outside)} {'is' if len(outside) == 1 else 'are'} not legal in "
                f"{regulation.label} (no usage data in this regulation); data from other "
                "regulations is never used."
            ),
        }

    def damage_calc(
        self,
        attacker_species: str,
        defender_species: str,
        move: str,
        attacker_item: str,
        attacker_nature: str,
    ) -> dict[str, Any]:
        """Deterministically compute damage for one attacker move vs a defender.

        Backed by @smogon/calc. Use this for exact KO-chance/roll questions the
        precomputed context does not already answer — e.g. a hypothetical held
        item or nature.

        Args:
            attacker_species: Attacking Pokemon species, e.g. "Garchomp".
            defender_species: Defending Pokemon species, e.g. "Sinistcha".
            move: Move name, e.g. "Earthquake".
            attacker_item: Attacker's held item, e.g. "Life Orb". Pass "" if
                unknown or not relevant to the question.
            attacker_nature: Attacker's nature, e.g. "Adamant". Pass "" if
                unknown or not relevant to the question.
        """
        refused = self._not_legal([attacker_species, defender_species])
        if refused is not None:
            return refused
        try:
            result = self._calc.calculate(
                CalcRequest(
                    gen=self._gen,
                    attacker=PokemonSet(
                        species=attacker_species,
                        item=attacker_item or None,
                        nature=attacker_nature or None,
                    ),
                    defender=PokemonSet(species=defender_species),
                    move=move,
                )
            )
        except (CalcEngineError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result.model_dump(mode="json")}

    def chaos_meta_stats(self, species: list[str]) -> dict[str, Any]:
        """Return Top-N Chaos usage stats for the given species.

        Covers abilities, items, moves, EV/nature spreads and checks/counters.

        Args:
            species: Species names to summarize from Chaos usage stats.
        """
        refused = self._not_legal(list(species))
        if refused is not None:
            return refused
        try:
            context = self._meta.build_match_context(
                list(species), metagame=self._scope.format_id
            )
        except ChaosDataError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **context.model_dump(mode="json")}

    def smogon_strategy(self, species: str) -> dict[str, Any]:
        """Return Smogon-derived archetypes and common teammates for one species.

        Args:
            species: The Pokemon species to describe strategically.
        """
        refused = self._not_legal([species])
        if refused is not None:
            return refused
        try:
            strategy = self._strategy.get_strategy(species, metagame=self._scope.format_id)
        except StrategyKnowledgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **strategy.model_dump(mode="json")}

    def functions(self) -> list[ToolFunction]:
        """The tools as bound functions (name + docstring + signature intact)."""
        return [self.damage_calc, self.chaos_meta_stats, self.smogon_strategy]
