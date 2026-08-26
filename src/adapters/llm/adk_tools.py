"""Google ADK function-tool wrappers around the deterministic domain ports.

Mirrors ``src/adapters/llm/langchain_tools.py``'s role for the ADK backend
(see ``src/services/adk_orchestrator.py``): the explanation stage's ADK
``LlmAgent`` may call these mid-answer for a question the precomputed
context doesn't already cover. ADK auto-wraps a plain, type-hinted Python
function (with a Google-style docstring) into a callable tool straight off
its signature — no separate schema class needed, unlike LangChain's
``StructuredTool``.

Two constraints specific to this SDK, both worth stating explicitly since
they make this file's shape differ from ``langchain_tools.py`` even though
the underlying ports and degrade convention are identical:

- Every ADK tool function must return a plain ``dict`` (the SDK's own
  requirement) — kept as the exact same ``{"ok": True, ...}`` /
  ``{"ok": False, "error": str}`` shape used everywhere else in this
  pipeline (the Node IPC boundary, ``langchain_tools.py``), so
  ``AgentToolInvocation.ok`` has one uniform signal to read regardless of
  orchestration backend.
- No parameter may carry a default value: the Gemini API's function-calling
  schema (unlike LangChain's/OpenAI's own) rejects a declaration that has
  one (confirmed against the live google/adk-python issue tracker: "Default
  value is not supported in function declaration schema for Google AI").
  The optional string arguments ``langchain_tools.py`` declares with
  defaults are therefore plain *required* ``str`` here, with the docstring
  instructing the model to pass ``""`` when it has nothing to supply —
  treated as "unset" inside the function body, same effective behavior.
"""

from __future__ import annotations

from typing import Any

from src.domain.exceptions import CalcEngineError, ChaosDataError, StrategyKnowledgeError
from src.domain.interfaces import (
    CalcEngineAdapter,
    MetaStatsProvider,
    StrategyKnowledgeProvider,
)
from src.domain.models import CalcRequest, PokemonSet


def build_adk_tools(
    *,
    calc_engine: CalcEngineAdapter,
    meta_provider: MetaStatsProvider,
    strategy_provider: StrategyKnowledgeProvider,
    default_gen: int = 9,
) -> list[Any]:
    """Return plain functions ready to pass straight into ``Agent(tools=[...])``.

    Typed ``list[Any]`` rather than ``list[Callable[..., dict]]``: ADK's own
    ``Agent.tools`` field type (``list[Callable[..., Any] | BaseTool |
    BaseToolset]``) would otherwise reject this list at the call site under
    mypy's strict, invariant-``list`` checking, even though every element
    genuinely is such a callable.
    """

    # Every function returns {"ok": True, ...fields} or {"ok": False, "error":
    # str} — never lets a domain exception propagate into the agent loop.
    # See this module's own docstring for why the shape mirrors
    # langchain_tools.py's identical convention.
    def damage_calc(
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
            result = calc_engine.calculate(
                CalcRequest(
                    gen=default_gen,
                    attacker=PokemonSet(
                        species=attacker_species,
                        item=attacker_item or None,
                        nature=attacker_nature or None,
                    ),
                    defender=PokemonSet(species=defender_species),
                    move=move,
                )
            )
            return {"ok": True, **result.model_dump()}
        except CalcEngineError as exc:
            return {"ok": False, "error": str(exc)}

    def chaos_meta_stats(species: list[str]) -> dict[str, Any]:
        """Return Top-N Chaos usage stats for the given species.

        Covers abilities, items, moves, EV/nature spreads and checks/counters.

        Args:
            species: Species names to summarize from Chaos usage stats.
        """
        try:
            context = meta_provider.build_match_context(list(species))
            return {"ok": True, **context.model_dump()}
        except ChaosDataError as exc:
            return {"ok": False, "error": str(exc)}

    def smogon_strategy(species: str) -> dict[str, Any]:
        """Return Smogon-derived archetypes and common teammates for one species.

        Args:
            species: The Pokemon species to describe strategically.
        """
        try:
            strategy = strategy_provider.get_strategy(species)
            return {"ok": True, **strategy.model_dump()}
        except StrategyKnowledgeError as exc:
            return {"ok": False, "error": str(exc)}

    return [damage_calc, chaos_meta_stats, smogon_strategy]
