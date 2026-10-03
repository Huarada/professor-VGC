"""The deterministic evidence stage shared by every backend.

Builds the ground truth once: metagame context for every in-play Pokemon,
field-aware verdicts, per-turn re-checks, Protect reads, Smogon strategy and
(on request) improvement suggestions. Backends own only how they select and
explain. Also the regulation boundary (ADR-035): one regulation per
analysis, data queried with its format only, evidence filtered to its legal
Pokemon, and the answer checked before it is returned.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from src.domain.interfaces import (
    CalcEngineAdapter,
    ConversationMemory,
    LogParser,
    MetaStatsProvider,
    RegulationCatalog,
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
    MetaContext,
    PokemonMetaSummary,
    RegulationInfo,
    SelectionPlan,
    SmogonStrategy,
)
from src.domain.regulation import RegulationRoster, RegulationScope
from src.services.battle_context import candidate_species, context_species, outcome_summary
from src.services.concept_tracking import recurring_concepts
from src.services.matchup_evaluator import MatchupEvaluator, collect_strategies
from src.services.regulation_guard import RegulationGuard
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
        scope: RegulationScope | None = None,
        regulation_catalog: RegulationCatalog | None = None,
        guard: RegulationGuard | None = None,
    ) -> None:
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._suggestion_source = suggestion_source
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)
        self._simulator = TurnReplaySimulator(calc_engine, default_gen)
        self.scope = scope or RegulationScope()
        self._catalog = regulation_catalog
        self._guard = guard or RegulationGuard(None)

    # -- regulation ---------------------------------------------------------- #

    def prepare(self, parser: LogParser, request: AnalysisRequest) -> GameState:
        """Parse the replay and bind its regulation before any LLM call, so a
        mismatching replay costs nothing.

        Raises:
            RegulationMismatchError: Pinned regulation and replay disagree.
        """
        game_state = parse_replay(parser, request)
        has_replay = request.replay_json is not None or request.replay_raw_text is not None
        self.scope.bind(game_state.format_id, has_replay=has_replay)
        return game_state

    def _data_format(self, game_state: GameState) -> str:
        """The one format every data source is queried with: the bound
        regulation's Bo1 format (Bo3 replays share it), else the replay's own."""
        return self.scope.format_id or game_state.format_id

    def _roster(self) -> RegulationRoster:
        if self._catalog is None or self.scope.format_id is None:
            return RegulationRoster()
        return self._catalog.roster(self.scope.format_id)

    def _regulation_info(self, roster: RegulationRoster) -> RegulationInfo | None:
        regulation = self.scope.current
        if regulation is None:
            return None
        return RegulationInfo(
            format_id=regulation.format_id,
            label=regulation.label,
            strict=self.scope.strict,
            legal_species=self._guard.legal_display_names(roster.legal) if not roster.empty else [],
        )

    # -- evidence ------------------------------------------------------------ #

    def assemble(
        self,
        *,
        request: AnalysisRequest,
        game_state: GameState,
        selection: SelectionPlan,
        history: Sequence[ChatMessage],
    ) -> AnalysisEvidence:
        data_format = self._data_format(game_state)
        roster = self._roster()
        # Metagame/Smogon context covers EVERY Pokemon in play (both sides),
        # not just the selection focus, so the AI can reason about the full game.
        context_mons = context_species(game_state, selection.focus_species)
        meta_context = _filter_meta(
            self._meta.build_match_context(
                context_mons, metagame=data_format, rating=game_state.rating
            ),
            roster,
        )
        turn_checks = self._simulator.simulate(game_state, meta_context)
        return AnalysisEvidence(
            selection=selection,
            meta_context=meta_context,
            verdicts=self._evaluator.evaluate(game_state, selection, meta_context),
            turn_checks=turn_checks,
            protect_reads=self._simulator.build_protect_reads(turn_checks, game_state),
            strategies=[
                _filter_strategy(s, roster)
                for s in collect_strategies(
                    self._strategy, context_mons, metagame=data_format,
                    question=request.question,
                )
            ],
            improvement_suggestions=_filter_improvements(
                self._improvements(request, selection, game_state, data_format), roster
            ),
            recurring_concepts=recurring_concepts(history, request.question),
            battle_result=outcome_summary(game_state),
            regulation=self._regulation_info(roster),
        )

    def _improvements(
        self, request: AnalysisRequest, selection: SelectionPlan, game_state: GameState,
        data_format: str,
    ) -> dict[str, Any]:
        if self._suggestion_source is None or not wants_suggestions(request.question):
            return {}
        species = selection.focus_species or candidate_species(game_state)
        return build_improvement_context(self._suggestion_source, species[:6], data_format)

    # -- explanation guard ---------------------------------------------------- #

    def explain_within_regulation(
        self,
        explain: Callable[[str], str],
        evidence: AnalysisEvidence,
        game_state: GameState,
    ) -> tuple[str, list[str]]:
        """Run ``explain``; if the answer names an illegal Pokemon, retry once with a
        correction note. Returns the answer and any remaining warnings.
        """
        answer = explain("")
        roster = self._roster()
        if evidence.regulation is None:
            return answer, []
        if roster.empty:
            return answer, [
                f"No usage data for {evidence.regulation.label} is loaded, so Pokemon "
                "legality could not be verified for this answer."
            ]
        in_game = game_state.involved_species()
        violations = self._guard.violations(answer, roster, in_game)
        if not violations:
            return answer, []
        answer = explain(self._guard.correction_note(violations, evidence.regulation.label))
        remaining = self._guard.violations(answer, roster, in_game)
        if not remaining:
            return answer, []
        return answer, [
            f"{name} is not legal in {evidence.regulation.label} (no usage data in this "
            "regulation) but is mentioned in the answer — treat that part as unreliable."
            for name in remaining
        ]


def _filter_meta(meta: MetaContext, roster: RegulationRoster) -> MetaContext:
    """Drop threats/counters that are not legal in the regulation."""
    if roster.empty:
        return meta

    def clean(summary: PokemonMetaSummary) -> PokemonMetaSummary:
        threats = {k: v for k, v in summary.threats_winrate.items() if roster.allows(k)}
        return summary.model_copy(update={"threats_winrate": threats})

    return meta.model_copy(update={
        "pokemon_stats": {k: clean(v) for k, v in meta.pokemon_stats.items()},
        "current_tier_stats": {k: clean(v) for k, v in meta.current_tier_stats.items()},
    })


def _filter_strategy(strategy: SmogonStrategy, roster: RegulationRoster) -> SmogonStrategy:
    if roster.empty:
        return strategy
    return strategy.model_copy(update={
        "common_teammates": [t for t in strategy.common_teammates if roster.allows(t)],
    })


def _filter_improvements(improvements: dict[str, Any], roster: RegulationRoster) -> dict[str, Any]:
    if roster.empty:
        return improvements
    cleaned: dict[str, Any] = {}
    for species, entry in improvements.items():
        entry = dict(entry)
        teammates = entry.get("teammates_usage")
        if isinstance(teammates, dict):
            entry["teammates_usage"] = {k: v for k, v in teammates.items() if roster.allows(k)}
        cleaned[species] = entry
    return cleaned


def parse_replay(parser: LogParser, request: AnalysisRequest) -> GameState:
    """Parse the request's replay (JSON wins over raw text); empty state if none."""
    source = request.replay_json if request.replay_json is not None else request.replay_raw_text
    if source is None:
        return GameState()
    return parser.parse(source)


def build_explanation_context(evidence: AnalysisEvidence) -> dict[str, Any]:
    """Assemble the trusted-context payload for the 2nd AI."""
    context: dict[str, Any] = {
        "battle_result": evidence.battle_result,
        "turn_by_turn_checks": [t.model_dump(mode="json") for t in evidence.turn_checks],
        "protect_reads": [p.model_dump(mode="json") for p in evidence.protect_reads],
        "meta_context": evidence.meta_context.model_dump(mode="json"),
        "deterministic_verdicts": [v.model_dump(mode="json") for v in evidence.verdicts],
        # retrieval_note is UI provenance only; the answer must not narrate plumbing.
        "strategies": [
            s.model_dump(mode="json", exclude={"retrieval_note"}) for s in evidence.strategies
        ],
        # Usually [] (see concept_tracking.py).
        "recurring_concepts": list(evidence.recurring_concepts),
        "improvement_suggestions": dict(evidence.improvement_suggestions),
        "selection_rationale": evidence.selection.rationale,
    }
    if evidence.regulation is not None:
        context["regulation"] = evidence.regulation.model_dump(mode="json")
    return context


def build_explanation_input(question: str, evidence: AnalysisEvidence, correction: str = "") -> str:
    """Render the human-turn text for the explanation stage (shared wording)."""
    context = build_explanation_context(evidence)
    text = (
        f"User question: {question or '(general analysis)'}\n\n"
        "Trusted context (JSON):\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Write the final ProfessorVGC explanation."
    )
    return f"{text}\n\n{correction}" if correction else text


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
    regulation_warnings: Sequence[str] = (),
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
        regulation=evidence.regulation,
        regulation_warnings=list(regulation_warnings),
    )
