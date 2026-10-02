"""The deterministic evidence stage shared by every orchestration backend.

Every :class:`~src.domain.interfaces.AnalysisPipeline` (native, LangChain,
Google ADK) runs the exact same middle: metagame context for every in-play
Pokemon, field-aware matchup verdicts, the per-turn re-checks, Protect reads,
Smogon strategy and (on request) improvement suggestions. It lives here,
once, so a new piece of evidence is added in one place and the backends can
never drift apart. Each backend only owns HOW it runs selection and
explanation; this module owns WHAT ground truth they explain.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from src.domain.interfaces import (
    CalcEngineAdapter,
    ConversationMemory,
    LogParser,
    MetaStatsProvider,
    SmogonSuggestionSource,
    StrategyKnowledgeProvider,
)
from src.domain.models import (
    AgentToolInvocation,
    AnalysisEvidence,
    AnalysisRequest,
    AnalysisResult,
    ChatMessage,
    GameState,
    SelectionPlan,
)
from src.services.battle_context import candidate_species, context_species, outcome_summary
from src.services.concept_tracking import recurring_concepts
from src.services.matchup_evaluator import MatchupEvaluator, collect_strategies
from src.services.suggestion_service import build_improvement_context, wants_suggestions
from src.services.turn_simulator import TurnReplaySimulator


class GroundTruthAssembler:
    """Builds the :class:`AnalysisEvidence` bundle for one analysis turn."""

    def __init__(
        self,
        *,
        meta_provider: MetaStatsProvider,
        calc_engine: CalcEngineAdapter,
        strategy_provider: StrategyKnowledgeProvider,
        default_gen: int = 9,
        suggestion_source: SmogonSuggestionSource | None = None,
    ) -> None:
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._suggestion_source = suggestion_source
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)
        self._simulator = TurnReplaySimulator(calc_engine, default_gen)

    def assemble(
        self,
        *,
        request: AnalysisRequest,
        game_state: GameState,
        selection: SelectionPlan,
        history: Sequence[ChatMessage],
    ) -> AnalysisEvidence:
        # Metagame/Smogon context covers EVERY Pokemon in play (both sides),
        # not just the selection focus, so the AI can reason about the full game.
        context_mons = context_species(game_state, selection.focus_species)
        meta_context = self._meta.build_match_context(
            context_mons, metagame=game_state.format_id, rating=game_state.rating
        )
        turn_checks = self._simulator.simulate(game_state, meta_context)
        return AnalysisEvidence(
            selection=selection,
            meta_context=meta_context,
            verdicts=self._evaluator.evaluate(game_state, selection, meta_context),
            turn_checks=turn_checks,
            protect_reads=self._simulator.build_protect_reads(turn_checks, game_state),
            strategies=collect_strategies(
                self._strategy, context_mons, metagame=game_state.format_id,
                question=request.question,
            ),
            improvement_suggestions=self._improvements(request, selection, game_state),
            recurring_concepts=recurring_concepts(history, request.question),
            battle_result=outcome_summary(game_state),
        )

    def _improvements(
        self, request: AnalysisRequest, selection: SelectionPlan, game_state: GameState
    ) -> dict[str, Any]:
        if self._suggestion_source is None or not wants_suggestions(request.question):
            return {}
        species = selection.focus_species or candidate_species(game_state)
        return build_improvement_context(
            self._suggestion_source, species[:6], game_state.format_id
        )


def parse_replay(parser: LogParser, request: AnalysisRequest) -> GameState:
    """Parse the request's replay (JSON wins over raw text); empty state if none."""
    source = request.replay_json if request.replay_json is not None else request.replay_raw_text
    if source is None:
        return GameState()
    return parser.parse(source)


def build_explanation_context(evidence: AnalysisEvidence) -> dict[str, Any]:
    """Assemble the trusted-context payload for the 2nd AI."""
    return {
        "battle_result": evidence.battle_result,
        "turn_by_turn_checks": [t.model_dump(mode="json") for t in evidence.turn_checks],
        "protect_reads": [p.model_dump(mode="json") for p in evidence.protect_reads],
        "meta_context": evidence.meta_context.model_dump(mode="json"),
        "deterministic_verdicts": [v.model_dump(mode="json") for v in evidence.verdicts],
        # retrieval_note is deliberately excluded here: it's provenance
        # metadata for the UI's own "Strategies" debug expander (which
        # dumps SmogonStrategy in full via result.strategies), not
        # something the LLM should ever narrate about — the explanation
        # must read as expert analysis, not describe its own plumbing.
        "strategies": [
            s.model_dump(mode="json", exclude={"retrieval_note"}) for s in evidence.strategies
        ],
        # [] the overwhelming majority of turns (first-ever question, or a
        # question that doesn't repeat an earlier topic) — see
        # concept_tracking.py's own docstring for exactly what this is and
        # isn't a claim of.
        "recurring_concepts": list(evidence.recurring_concepts),
        "improvement_suggestions": dict(evidence.improvement_suggestions),
        "selection_rationale": evidence.selection.rationale,
    }


def build_explanation_input(question: str, evidence: AnalysisEvidence) -> str:
    """Render the human-turn text for the explanation stage (shared wording)."""
    context = build_explanation_context(evidence)
    return (
        f"User question: {question or '(general analysis)'}\n\n"
        "Trusted context (JSON):\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Write the final ProfessorVGC explanation."
    )


def remember_turn(memory: ConversationMemory, request: AnalysisRequest, answer: str) -> None:
    """Append this question/answer pair to the session's conversation memory."""
    memory.append(request.session_id, ChatMessage(role="user", content=request.question))
    memory.append(request.session_id, ChatMessage(role="assistant", content=answer))


def build_result(
    *,
    request: AnalysisRequest,
    evidence: AnalysisEvidence,
    answer: str,
    provider: str,
    agent_tool_calls: Sequence[AgentToolInvocation] = (),
) -> AnalysisResult:
    """The UI DTO for one completed analysis turn."""
    return AnalysisResult(
        session_id=request.session_id,
        question=request.question,
        answer=answer,
        selection=evidence.selection,
        meta_context=evidence.meta_context,
        verdicts=evidence.verdicts,
        strategies=evidence.strategies,
        turn_checks=evidence.turn_checks,
        protect_reads=evidence.protect_reads,
        agent_tool_calls=list(agent_tool_calls),
        battle_result=evidence.battle_result,
        provider=provider,
    )
