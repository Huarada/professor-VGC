"""Abstract contracts (ports) for every external dependency.

Business logic (services) depends only on these ``Protocol`` types, never on
concrete adapters. Concrete infrastructure is injected at composition time
(see ``src/services/container.py``). This is the Dependency Inversion
Principle: swapping the calc engine, the LLM vendor or the memory backend
touches only an adapter, never the domain or the services.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from src.domain.models import (
    AnalysisRequest,
    AnalysisResult,
    CalcRequest,
    ChatMessage,
    DamageResult,
    GameState,
    MetaContext,
    SelectionPlan,
    SmogonStrategy,
    SpeedComparison,
)


@runtime_checkable
class LogParser(Protocol):
    """Parses a raw Showdown replay/log into a deterministic GameState."""

    def parse(self, replay: dict[str, Any] | str) -> GameState:
        """Parse a replay into a structured :class:`GameState`.

        Raises:
            LogParsingError: If the replay cannot be interpreted.
        """
        ...


@runtime_checkable
class MetaStatsProvider(Protocol):
    """Provides compact metagame statistics for a set of species (Chaos)."""

    def build_match_context(
        self,
        species: Sequence[str],
        *,
        metagame: str | None = None,
        rating: int | None = None,
    ) -> MetaContext:
        """Build the consolidated metagame context for the given species.

        ``metagame`` selects the format (defaults to the newest available);
        ``rating`` selects the current ladder tier bracket. Raises:
            ChaosDataError: If the underlying dataset is missing/malformed.
        """
        ...


@runtime_checkable
class CalcEngineAdapter(Protocol):
    """Deterministic damage-calc engine (backed by Node ``@smogon/calc``)."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        """Run a single deterministic damage calculation.

        Raises:
            CalcEngineError: On transport failure or invalid engine output.
        """
        ...

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        """Deterministically compare the speed of attacker and defender.

        Raises:
            CalcEngineError: On transport failure or invalid engine output.
        """
        ...

    def forme_resolves(self, gen: int, species: str) -> bool:
        """Whether the engine's dex has real stat data for this exact forme
        string (e.g. a Mega Evolution) — vs. it being unresolvable, in which
        case a caller falls back to base stats.

        Raises:
            CalcEngineError: On transport failure or invalid engine output.
        """
        ...

    def close(self) -> None:
        """Release any long-lived engine resources (subprocess, sockets)."""
        ...


@runtime_checkable
class StrategyKnowledgeProvider(Protocol):
    """Narrative strategy/archetype knowledge from Smogon."""

    def get_strategy(
        self, species: str, *, metagame: str | None = None, question: str | None = None
    ) -> SmogonStrategy:
        """Return strategy knowledge for a species (walks reg fallback).

        Args:
            species: The Pokemon species to look up.
            metagame: Format id, for regulation-fallback resolution.
            question: The user's actual question, if any. Implementations
                that hold multiple candidate passages of prose (e.g. a
                semantic retriever over official Smogon analyses) may use
                this to select the most relevant ones instead of a fixed
                default; implementations with no such choice to make (e.g.
                Chaos-derived strategy, which has no free-text passages)
                simply ignore it. Never changes the STRUCTURED fields
                (common_sets/archetypes) — only ever narrows which prose
                becomes `overview`.

        Raises:
            StrategyKnowledgeError: If the source is unavailable.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Bring-your-own-key text embeddings.

    Used only by :class:`~src.adapters.smogon.semantic_strategy_retriever.
    SemanticStrategyRetriever` to rank candidate passages of official Smogon
    analysis prose against the user's actual question — a narrow, optional
    enhancement layered on top of the deterministic/probabilistic core, not
    a dependency of it.
    """

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order.

        Raises:
            LLMProviderError: On any transport/auth failure.
        """
        ...


@runtime_checkable
class ConversationMemory(Protocol):
    """Per-session conversational memory (the *NECESSITA MEMÓRIA* nodes)."""

    def load(self, session_id: str) -> list[ChatMessage]:
        """Return the ordered message history for a session."""
        ...

    def append(self, session_id: str, message: ChatMessage) -> None:
        """Append one message to a session's history."""
        ...

    def clear(self, session_id: str) -> None:
        """Drop all history for a session."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Bring-your-own-key LLM abstraction (OpenAI, Gemini, ...)."""

    name: str

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        """Return a completion string for the given prompt.

        Raises:
            LLMProviderError: On transport-level failure.
        """
        ...


@runtime_checkable
class SelectionStrategy(Protocol):
    """1st AI: decide which species/matchups the question is about."""

    def select(
        self,
        *,
        request: AnalysisRequest,
        game_state: GameState,
        history: Sequence[ChatMessage],
    ) -> SelectionPlan:
        """Produce a focused :class:`SelectionPlan`.

        Raises:
            LLMResponseValidationError: If the model output is unusable.
        """
        ...


@runtime_checkable
class AnalysisPipeline(Protocol):
    """End-to-end analysis orchestration port.

    Both the native :class:`AnalysisService` and the LangChain orchestrator
    implement this. The presentation layer depends only on this abstraction,
    so the orchestration technology can be swapped from configuration alone.
    """

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Run one full analysis turn and return the UI DTO."""
        ...
