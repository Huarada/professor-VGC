"""Framework-agnostic read-only tools over the deterministic domain ports.

The explanation agents (Google ADK and LangChain) may call these mid-answer
for a question the precomputed context doesn't already cover (a hypothetical
item, a different species, ...). This module is the ONE implementation of
the three tools; ``adk_tools.py`` and ``langchain_tools.py`` only adapt it to
their framework's tool format.

Conventions every tool follows:

- Returns a plain ``dict`` — ``{"ok": True, ...fields}`` or
  ``{"ok": False, "error": str}`` — and never lets a domain exception reach
  the agent loop. This mirrors the Node IPC boundary's degrade convention, so
  ``AgentToolInvocation.ok`` has one uniform signal across backends.
- No parameter has a default value: the Gemini function-calling schema
  rejects declarations with defaults ("Default value is not supported in
  function declaration schema for Google AI"). Optional strings are plain
  required ``str`` arguments where ``""`` means "unset".
- Google-style docstrings: ADK builds the tool description from them.
"""

from __future__ import annotations

from typing import Any, Callable

from src.domain.exceptions import CalcEngineError, ChaosDataError, StrategyKnowledgeError
from src.domain.interfaces import (
    CalcEngineAdapter,
    MetaStatsProvider,
    StrategyKnowledgeProvider,
)
from src.domain.models import CalcRequest, PokemonSet

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
    ) -> None:
        self._calc = calc_engine
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._gen = default_gen

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
        try:
            context = self._meta.build_match_context(list(species))
        except ChaosDataError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **context.model_dump(mode="json")}

    def smogon_strategy(self, species: str) -> dict[str, Any]:
        """Return Smogon-derived archetypes and common teammates for one species.

        Args:
            species: The Pokemon species to describe strategically.
        """
        try:
            strategy = self._strategy.get_strategy(species)
        except StrategyKnowledgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **strategy.model_dump(mode="json")}

    def functions(self) -> list[ToolFunction]:
        """The tools as bound functions (name + docstring + signature intact)."""
        return [self.damage_calc, self.chaos_meta_stats, self.smogon_strategy]
