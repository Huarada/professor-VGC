"""LangChain adapter for :class:`~src.adapters.llm.evidence_tools.EvidenceTools`.

Wraps the shared tool core as ``StructuredTool``s for the LangChain
explanation agent (``langchain.agents.create_agent``, ADR-028). The only
LangChain-specific concern here is the argument schema: unlike Gemini's
function declarations, LangChain/OpenAI schemas allow optional arguments, so
``attacker_item``/``attacker_nature`` are optional here and mapped to the
core's ``""`` = unset convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.adapters.llm.evidence_tools import EvidenceTools

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool


class _CalcToolArgs(BaseModel):
    attacker_species: str = Field(description="Attacking Pokemon species")
    defender_species: str = Field(description="Defending Pokemon species")
    move: str = Field(description="Move name, e.g. 'Earthquake'")
    attacker_item: str | None = Field(default=None, description="Attacker held item")
    attacker_nature: str | None = Field(default=None, description="Attacker nature")


class _MetaToolArgs(BaseModel):
    species: list[str] = Field(description="Species to summarize from Chaos stats")


class _StrategyToolArgs(BaseModel):
    species: str = Field(description="Species to describe strategically")


def build_langchain_tools(tools: EvidenceTools) -> list[StructuredTool]:
    """Return StructuredTools bound to the shared deterministic tool core."""
    from langchain_core.tools import StructuredTool

    def _calc(
        attacker_species: str,
        defender_species: str,
        move: str,
        attacker_item: str | None = None,
        attacker_nature: str | None = None,
    ) -> dict[str, Any]:
        return tools.damage_calc(
            attacker_species, defender_species, move,
            attacker_item or "", attacker_nature or "",
        )

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
            func=tools.chaos_meta_stats,
            name="chaos_meta_stats",
            description=(
                "Return Top-N Chaos usage stats (abilities/items/moves/spreads/"
                "counters) for the given species."
            ),
            args_schema=_MetaToolArgs,
        ),
        StructuredTool.from_function(
            func=tools.smogon_strategy,
            name="smogon_strategy",
            description="Return Smogon-derived archetypes and common teammates.",
            args_schema=_StrategyToolArgs,
        ),
    ]
