"""Analysis orchestrator (native backend) — one implementation of the pipeline.

Depends ONLY on domain Protocols. Satisfies the
:class:`~src.domain.interfaces.AnalysisPipeline` port, so it is interchangeable
with the LangChain orchestrator.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from src.adapters.llm.prompts import load_prompt
from src.domain.interfaces import (
    CalcEngineAdapter,
    ConversationMemory,
    LLMProvider,
    LogParser,
    MetaStatsProvider,
    SelectionStrategy,
    StrategyKnowledgeProvider,
)
from src.domain.models import (
    AnalysisRequest,
    AnalysisResult,
    ChatMessage,
    GameState,
    MatchupVerdict,
    MetaContext,
    ProtectRead,
    SelectionPlan,
    SmogonStrategy,
    TurnCheck,
)
from src.services.battle_context import candidate_species, context_species, outcome_summary
from src.services.concept_tracking import recurring_concepts
from src.services.suggestion_service import (
    SmogonSuggestionSource,
    build_improvement_context,
    wants_suggestions,
)
from src.services.turn_simulator import TurnReplaySimulator
from src.services.matchup_evaluator import MatchupEvaluator, collect_strategies


class AnalysisService:
    """Orchestrates parse -> select -> calc -> validate -> explain (native)."""

    def __init__(
        self,
        *,
        parser: LogParser,
        selector: SelectionStrategy,
        meta_provider: MetaStatsProvider,
        calc_engine: CalcEngineAdapter,
        strategy_provider: StrategyKnowledgeProvider,
        llm: LLMProvider,
        memory: ConversationMemory,
        default_gen: int = 9,
        suggestion_source: SmogonSuggestionSource | None = None,
    ) -> None:
        self._parser = parser
        self._selector = selector
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._llm = llm
        self._memory = memory
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)
        self._simulator = TurnReplaySimulator(calc_engine, default_gen)
        self._suggestion_source = suggestion_source
        self._explanation_system = load_prompt("explanation_system")

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Run one full analysis turn and return the UI DTO."""
        history = self._memory.load(request.session_id)
        game_state = self._parse(request)

        selection = self._selector.select(
            request=request, game_state=game_state, history=history
        )
        # Metagame/Smogon context covers EVERY Pokemon in play (both sides),
        # not just the selection focus, so the AI can reason about the full game.
        context_mons = context_species(game_state, selection.focus_species)
        meta_context = self._meta.build_match_context(
            context_mons, metagame=game_state.format_id, rating=game_state.rating
        )
        verdicts = self._evaluator.evaluate(game_state, selection, meta_context)
        turn_checks = self._simulator.simulate(game_state, meta_context)
        protect_reads = self._simulator.build_protect_reads(turn_checks, game_state)
        strategies = collect_strategies(
            self._strategy, context_mons, metagame=game_state.format_id,
            question=request.question,
        )

        improvement = self._improvements(request, selection, game_state)
        answer = self._explain(
            request=request,
            history=history,
            game_state=game_state,
            selection=selection,
            meta_context=meta_context,
            verdicts=verdicts,
            turn_checks=turn_checks,
            protect_reads=protect_reads,
            strategies=strategies,
            improvement=improvement,
        )

        self._memory.append(
            request.session_id, ChatMessage(role="user", content=request.question)
        )
        self._memory.append(
            request.session_id, ChatMessage(role="assistant", content=answer)
        )

        return AnalysisResult(
            session_id=request.session_id,
            question=request.question,
            answer=answer,
            selection=selection,
            meta_context=meta_context,
            verdicts=verdicts,
            strategies=strategies,
            turn_checks=turn_checks,
            protect_reads=protect_reads,
            battle_result=outcome_summary(game_state),
            provider=getattr(self._llm, "name", request.provider),
        )

    def _parse(self, request: AnalysisRequest) -> GameState:
        source = (
            request.replay_json
            if request.replay_json is not None
            else request.replay_raw_text
        )
        if source is None:
            return GameState()
        return self._parser.parse(source)

    def _improvements(
        self, request: AnalysisRequest, selection: SelectionPlan, game_state: GameState
    ) -> dict[str, Any] | None:
        if self._suggestion_source is None or not wants_suggestions(request.question):
            return None
        species = selection.focus_species or candidate_species(game_state)
        return build_improvement_context(
            self._suggestion_source, species[:6], game_state.format_id
        )

    def _explain(
        self,
        *,
        request: AnalysisRequest,
        history: Sequence[ChatMessage],
        game_state: GameState,
        selection: SelectionPlan,
        meta_context: MetaContext,
        verdicts: Sequence[MatchupVerdict],
        turn_checks: Sequence[TurnCheck],
        protect_reads: Sequence[ProtectRead] = (),
        strategies: Sequence[SmogonStrategy] = (),
        improvement: dict[str, Any] | None = None,
    ) -> str:
        context = build_explanation_context(
            selection, meta_context, verdicts, strategies,
            battle_result=outcome_summary(game_state),
            turn_checks=turn_checks,
            protect_reads=protect_reads,
            improvement_suggestions=improvement,
            recurring_concepts=recurring_concepts(history, request.question),
        )
        user_turn = ChatMessage(
            role="user",
            content=build_explanation_input(request.question, context),
        )
        return self._llm.complete(
            system=self._explanation_system,
            messages=[*history, user_turn],
            temperature=0.3,
            json_mode=False,
        )


def _noop() -> None:  # pragma: no cover - placeholder anchor
    return None


def build_explanation_context(
    selection: SelectionPlan,
    meta_context: MetaContext,
    verdicts: Sequence[MatchupVerdict],
    strategies: Sequence[SmogonStrategy],
    battle_result: str = "",
    turn_checks: Sequence[TurnCheck] | None = None,
    protect_reads: Sequence[ProtectRead] | None = None,
    improvement_suggestions: dict[str, Any] | None = None,
    recurring_concepts: Sequence[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Assemble the trusted-context payload for the 2nd AI (shared)."""
    return {
        "battle_result": battle_result,
        "turn_by_turn_checks": [t.model_dump() for t in (turn_checks or [])],
        "protect_reads": [p.model_dump() for p in (protect_reads or [])],
        "meta_context": meta_context.model_dump(),
        "deterministic_verdicts": [v.model_dump() for v in verdicts],
        # retrieval_note is deliberately excluded here: it's provenance
        # metadata for the UI's own "Strategies" debug expander (which
        # dumps SmogonStrategy in full via result.strategies), not
        # something the LLM should ever narrate about — the explanation
        # must read as expert analysis, not describe its own plumbing.
        "strategies": [s.model_dump(exclude={"retrieval_note"}) for s in strategies],
        # [] the overwhelming majority of turns (first-ever question, or a
        # question that doesn't repeat an earlier topic) — see
        # concept_tracking.py's own docstring for exactly what this is and
        # isn't a claim of.
        "recurring_concepts": list(recurring_concepts or []),
        "improvement_suggestions": improvement_suggestions or {},
        "selection_rationale": selection.rationale,
    }


def build_explanation_input(question: str, context: dict[str, Any]) -> str:
    """Render the human-turn text for the explanation stage (shared wording)."""
    return (
        f"User question: {question or '(general analysis)'}\n\n"
        "Trusted context (JSON):\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Write the final ProfessorVGC explanation."
    )
