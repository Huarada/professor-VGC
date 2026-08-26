"""LangChain Tool wrappers around the deterministic domain ports.

Exposes the calc engine, Chaos stats and Smogon strategy as ``StructuredTool``s
so a LangChain agent (``langchain.agents.create_agent``) can call them on demand
for interactive follow-up questions. The main pipeline still runs these
deterministically; the tools are the agentic escape hatch.

Wired into :class:`~src.services.langchain_orchestrator.LangChainAnalysisOrchestrator`
(see ADR-028) — the explanation stage there is a bounded ``create_agent`` loop
built from exactly these three tools, so the LangChain backend can reach back
into live calc/Chaos/Smogon data mid-answer for a question the precomputed
context doesn't already cover, while the native :class:`AnalysisService` has
no such loop. Every call degrades to ``{"ok": False, "error": ...}`` rather
than raising, matching the Node IPC boundary's own convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from pydantic import BaseModel, Field

from src.domain.exceptions import CalcEngineError, ChaosDataError, StrategyKnowledgeError
from src.domain.interfaces import (
    CalcEngineAdapter,
    MetaStatsProvider,
    StrategyKnowledgeProvider,
)
from src.domain.models import CalcRequest, PokemonSet

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool


class _CalcToolArgs(BaseModel):
    attacker_species: str = Field(description="Attacking Pokemon species")
    defender_species: str = Field(description="Defending Pokemon species")
    move: str = Field(description="Move name, e.g. 'Earthquake'")
    attacker_item: str | None = Field(default=None, description="Attacker held item")
    attacker_nature: str | None = Field(default=None, description="Attacker nature")
    gen: int = Field(default=9, description="Generation number")


class _MetaToolArgs(BaseModel):
    species: list[str] = Field(description="Species to summarize from Chaos stats")


class _StrategyToolArgs(BaseModel):
    species: str = Field(description="Species to describe strategically")


def build_langchain_tools(
    *,
    calc_engine: CalcEngineAdapter,
    meta_provider: MetaStatsProvider,
    strategy_provider: StrategyKnowledgeProvider,
    default_gen: int = 9,
) -> list[StructuredTool]:
    """Return StructuredTools bound to the given deterministic adapters."""
    from langchain_core.tools import StructuredTool

    # Every function returns {"ok": True, ...fields} or {"ok": False, "error":
    # str} — never lets a domain exception propagate into the agent loop.
    # This mirrors the exact degrade convention already used at the Node IPC
    # boundary (src/adapters/node_ipc.py / calc_server.js): a failed on-demand
    # lookup is reported to the model as data, not as a crash, and the turn
    # still completes. It also gives AgentToolInvocation.ok (see models.py) a
    # single, uniform signal to parse back out of every tool's ToolMessage.
    def _calc(
        attacker_species: str,
        defender_species: str,
        move: str,
        attacker_item: str | None = None,
        attacker_nature: str | None = None,
        gen: int = default_gen,
    ) -> dict[str, Any]:
        try:
            result = calc_engine.calculate(
                CalcRequest(
                    gen=gen,
                    attacker=PokemonSet(
                        species=attacker_species,
                        item=attacker_item,
                        nature=attacker_nature,
                    ),
                    defender=PokemonSet(species=defender_species),
                    move=move,
                )
            )
            return {"ok": True, **result.model_dump()}
        except CalcEngineError as exc:
            return {"ok": False, "error": str(exc)}

    def _meta(species: Sequence[str]) -> dict[str, Any]:
        try:
            context = meta_provider.build_match_context(list(species))
            return {"ok": True, **context.model_dump()}
        except ChaosDataError as exc:
            return {"ok": False, "error": str(exc)}

    def _strategy(species: str) -> dict[str, Any]:
        try:
            strategy = strategy_provider.get_strategy(species)
            return {"ok": True, **strategy.model_dump()}
        except StrategyKnowledgeError as exc:
            return {"ok": False, "error": str(exc)}

    return [
        StructuredTool.from_function(
            func=_calc,
            name="damage_calc",
            description=(
                "Deterministically compute damage for one attacker move vs a "
                "defender using @smogon/calc. Use for exact KO/roll questions."
            ),
            args_schema=_CalcToolArgs,
        ),
        StructuredTool.from_function(
            func=_meta,
            name="chaos_meta_stats",
            description=(
                "Return Top-N Chaos usage stats (abilities/items/moves/spreads/"
                "counters) for the given species."
            ),
            args_schema=_MetaToolArgs,
        ),
        StructuredTool.from_function(
            func=_strategy,
            name="smogon_strategy",
            description="Return Smogon-derived archetypes and common teammates.",
            args_schema=_StrategyToolArgs,
        ),
    ]
