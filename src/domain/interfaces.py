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
    MoveInfo,
    PokemonMetaSummary,
    SelectionPlan,
    SmogonStrategy,
    SpeedComparison,
)
from src.domain.regulation import RegulationRoster


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

    def move_info(self, gen: int, move: str) -> MoveInfo:
        """Static data for one move from the engine's own dex (category,
        spread target, Protect-family, speed control) — the single source
        for "is this a damaging move?", never a hand-kept list.

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
class RegulationCatalog(Protocol):
    """Which Pokemon belong to a regulation, from that regulation's own data."""

    def roster(self, format_id: str) -> RegulationRoster:
        """The regulation's legal species (from its own tiers only — empty when
        it has no data loaded, never another format's) plus every species the
        other regulations of the same game list.

        Raises:
            ChaosDataError: If the underlying dataset is unavailable.
        """
        ...


@runtime_checkable
class SpeciesCatalog(Protocol):
    """Every species name the engine knows, to recognize Pokemon in prose."""

    def species_names(self, gen: int) -> list[str]:
        """Display names (e.g. ``"Iron Hands"``, ``"Charizard-Mega-Y"``).

        Raises:
            CalcEngineError: On transport failure or invalid engine output.
        """
        ...


@runtime_checkable
class SmogonSuggestionSource(Protocol):
    """Official Smogon sets/usage stats used for team-improvement suggestions."""

    def get_sets(self, species: str, *, metagame: str | None = None) -> list[dict[str, Any]]:
        """Official competitive sets for a species.

        Raises:
            StrategyKnowledgeError: If the source is unavailable.
        """
        ...

    def get_stats(self, species: str, *, metagame: str | None = None) -> PokemonMetaSummary:
        """Official usage statistics for a species.

        Raises:
            StrategyKnowledgeError: If the source is unavailable.
        """
        ...

    def get_teammates(self, species: str, *, metagame: str | None = None) -> dict[str, float]:
        """Teammate usage percentages for a species.

        Raises:
            StrategyKnowledgeError: If the source is unavailable.
        """
        ...


@runtime_checkable
class PromptRepository(Protocol):
    """Source of the versioned prompt artifacts (never inlined in logic)."""

    def get(self, name: str) -> str:
        """Return the prompt text registered under ``name``.

        Raises:
            ConfigurationError: If no such prompt exists.
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

    The native :class:`AnalysisService`, the LangChain orchestrator and the
    Google ADK orchestrator implement this. The presentation layer depends only on this abstraction,
    so the orchestration technology can be swapped from configuration alone.
    """

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Run one full analysis turn and return the UI DTO."""
        ...
