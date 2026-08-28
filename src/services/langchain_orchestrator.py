"""LangChain orchestration backend.

Implements the same :class:`~src.domain.interfaces.AnalysisPipeline` port as the
native :class:`~src.services.analysis_service.AnalysisService`, but expresses the
LLM stages as **LCEL** (LangChain Expression Language) runnables:

    selection_chain   = build_messages | chat_model | JsonOutputParser
    explanation_agent = create_agent(chat_model, damage_calc/chaos_meta_stats/
                         smogon_strategy tools, system_prompt)   -- see ADR-028

The deterministic middle (Chaos context + damage/speed calc + Smogon strategy)
is reused verbatim from :class:`MatchupEvaluator`, so both backends yield
identical ground-truth numbers. The explanation stage is a bounded tool-calling
agent (ADR-028): it always receives the full precomputed ground truth exactly
like before, but may additionally reach back into the SAME deterministic ports
mid-answer for a question that precomputed context doesn't cover (a
hypothetical item, a different rating tier, ...). This capability is scoped to
this backend only — the native :class:`AnalysisService` has no agent loop.

The prompt-braces problem (the system prompts contain literal ``{ }`` JSON
examples) is avoided by composing messages as concrete objects inside a
``RunnableLambda`` instead of routing them through a templating parser.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Sequence

from src.adapters.llm.langchain_tools import build_langchain_tools
from src.adapters.llm.prompts import load_prompt
from src.domain.exceptions import LLMProviderError
from src.domain.interfaces import (
    CalcEngineAdapter,
    ConversationMemory,
    LogParser,
    MetaStatsProvider,
    StrategyKnowledgeProvider,
)
from src.domain.models import (
    AgentToolInvocation,
    AnalysisRequest,
    AnalysisResult,
    ChatMessage,
    GameState,
    SelectionPlan,
)
from src.services.analysis_service import (
    build_explanation_context,
    build_explanation_input,
)
from src.services.battle_context import (
    candidate_species,
    context_species,
    outcome_summary,
    rosters,
)
from src.services.concept_tracking import recurring_concepts
from src.services.matchup_evaluator import MatchupEvaluator, collect_strategies
from src.services.suggestion_service import (
    SmogonSuggestionSource,
    build_improvement_context,
    wants_suggestions,
)
from src.services.turn_simulator import TurnReplaySimulator
from src.services.selection_logic import (
    build_selection_input,
    parse_selection,
    sanitize_plan,
)

if TYPE_CHECKING:
    # Type-checking only — kept lazy at runtime (see the local imports below)
    # so this module stays importable without langchain_core installed,
    # matching src.adapters.llm.langchain_provider's same pattern.
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from langchain_core.runnables import Runnable


class LangChainAnalysisOrchestrator:
    """AnalysisPipeline implemented with LangChain: a plain LCEL chain for
    selection (1st AI), a bounded tool-calling agent for explanation (2nd AI,
    see ADR-028)."""

    def __init__(
        self,
        *,
        parser: LogParser,
        chat_model: BaseChatModel,
        meta_provider: MetaStatsProvider,
        calc_engine: CalcEngineAdapter,
        strategy_provider: StrategyKnowledgeProvider,
        memory: ConversationMemory,
        default_gen: int = 9,
        provider_name: str = "langchain",
        max_matchups: int = 6,
        suggestion_source: SmogonSuggestionSource | None = None,
        agent_max_steps: int = 10,
    ) -> None:
        self._parser = parser
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._memory = memory
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)
        self._simulator = TurnReplaySimulator(calc_engine, default_gen)
        self._suggestion_source = suggestion_source
        self._provider_name = provider_name
        self._max_matchups = max_matchups
        # LangGraph "steps" (one model turn or one tool turn each) the
        # explanation agent may take before its own run is cut off — not a
        # count of tool calls directly, but generous enough for a handful of
        # them (see ADR-028). Never applies to the selection stage, which
        # stays a single plain completion.
        self._agent_max_steps = agent_max_steps

        self._selection_system = load_prompt("selection_system")
        self._explanation_system = load_prompt("explanation_system")
        self._selection_chain = self._build_selection_chain(chat_model)
        self._explanation_agent = self._build_explanation_agent(
            chat_model, calc_engine, meta_provider, strategy_provider, default_gen
        )

    def _build_selection_chain(self, chat_model: BaseChatModel) -> Runnable[Any, Any]:
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.runnables import RunnableLambda

        return RunnableLambda(self._selection_messages) | chat_model | JsonOutputParser()

    def _build_explanation_agent(
        self,
        chat_model: BaseChatModel,
        calc_engine: CalcEngineAdapter,
        meta_provider: MetaStatsProvider,
        strategy_provider: StrategyKnowledgeProvider,
        default_gen: int,
    ) -> Any:
        """Build the explanation stage as a bounded tool-calling agent
        (ADR-028) instead of a bare chat-model completion. The only behavior
        change from before: the model may now additionally call damage_calc/
        chaos_meta_stats/smogon_strategy mid-answer for a question the
        precomputed context (see build_explanation_context) doesn't already
        cover. Every tool wraps the exact same deterministic ports this
        orchestrator already depends on (see langchain_tools.py's own
        docstring) — this never introduces a second, competing source of
        truth, only a second, on-demand way to reach the same one."""
        from langchain.agents import create_agent

        tools = build_langchain_tools(
            calc_engine=calc_engine,
            meta_provider=meta_provider,
            strategy_provider=strategy_provider,
            default_gen=default_gen,
        )
        system_prompt = (
            f"{self._explanation_system}\n\n"
            f"{load_prompt('explanation_agent_addendum')}"
        )
        return create_agent(model=chat_model, tools=tools, system_prompt=system_prompt)

    def _selection_messages(self, payload: dict[str, Any]) -> list[BaseMessage]:
        from langchain_core.messages import HumanMessage, SystemMessage

        from src.adapters.llm.langchain_provider import to_lc_messages

        return [
            SystemMessage(content=self._selection_system),
            *to_lc_messages(payload["history"]),
            HumanMessage(content=payload["input"]),
        ]

    def _agent_messages(
        self, history: Sequence[ChatMessage], user_input: str
    ) -> list[BaseMessage]:
        """Message list for the explanation AGENT — no leading SystemMessage
        here: create_agent's own `system_prompt` already prepends one."""
        from langchain_core.messages import HumanMessage

        from src.adapters.llm.langchain_provider import to_lc_messages

        return [*to_lc_messages(history), HumanMessage(content=user_input)]

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        history = self._memory.load(request.session_id)
        game_state = self._parse(request)

        selection = self._select(request, game_state, history)
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

        improvement = None
        if self._suggestion_source is not None and wants_suggestions(request.question):
            improvement = build_improvement_context(
                self._suggestion_source,
                (selection.focus_species or candidate_species(game_state))[:6],
                game_state.format_id,
            )
        context = build_explanation_context(
            selection, meta_context, verdicts, strategies,
            battle_result=outcome_summary(game_state),
            turn_checks=turn_checks,
            protect_reads=protect_reads,
            improvement_suggestions=improvement,
            recurring_concepts=recurring_concepts(history, request.question),
        )
        # The agent talks to the raw LangChain chat model directly (not
        # through OpenAIProvider/GeminiProvider, which already wrap SDK
        # errors) — this is the one place a provider failure (rate limit,
        # exhausted quota, auth, network) would otherwise reach the UI as a
        # raw, unhandled SDK exception instead of the ProfessorVGCError the
        # presentation layer already knows how to render. A bad/failed tool
        # call inside the loop does NOT reach here — langchain_tools.py
        # degrades those to {"ok": False, "error": ...} instead of raising.
        try:
            agent_result = self._explanation_agent.invoke(
                {
                    "messages": self._agent_messages(
                        history, build_explanation_input(request.question, context)
                    )
                },
                config={"recursion_limit": self._agent_max_steps},
            )
        except Exception as exc:  # noqa: BLE001 - many SDK exception types
            raise LLMProviderError(
                f"The explanation model call failed ({request.provider}): {exc}"
            ) from exc

        agent_messages = agent_result.get("messages", [])
        final_content = agent_messages[-1].content if agent_messages else ""
        answer = final_content if isinstance(final_content, str) else str(final_content)
        agent_tool_calls = _extract_tool_invocations(agent_messages)

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
            agent_tool_calls=agent_tool_calls,
            battle_result=outcome_summary(game_state),
            provider=self._provider_name,
        )

    def _select(
        self,
        request: AnalysisRequest,
        game_state: GameState,
        history: Sequence[ChatMessage],
    ) -> SelectionPlan:
        species = candidate_species(game_state)
        side_of = game_state.side_of()
        try:
            raw = self._selection_chain.invoke(
                {
                    "history": history,
                    "input": build_selection_input(
                        game_state.format_id,
                        rosters(game_state),
                        request.question,
                        outcome_summary(game_state),
                    ),
                }
            )
        except Exception:  # noqa: BLE001 - parser/model failure -> deterministic fallback
            raw = "{}"
        plan = parse_selection(raw, species, side_of)
        return sanitize_plan(plan, species, self._max_matchups, side_of)

    def _parse(self, request: AnalysisRequest) -> GameState:
        source = (
            request.replay_json
            if request.replay_json is not None
            else request.replay_raw_text
        )
        if source is None:
            return GameState()
        return self._parser.parse(source)


def _extract_tool_invocations(messages: Sequence[Any]) -> list[AgentToolInvocation]:
    """Pull every on-demand tool call the explanation agent made this turn out
    of its LangGraph message trace, so the UI can flag it (ADR-028). Matches
    each ToolMessage back to the AIMessage.tool_calls entry that requested it
    (by tool_call_id) and reads the {"ok": ..., ...}/{"ok": False, "error":...}
    shape every tool in langchain_tools.py returns. Returns an empty list for
    the (overwhelmingly common) case where the agent never called a tool —
    which is also what the native AnalysisService always produces, since it
    has no agent loop at all."""
    from langchain_core.messages import AIMessage, ToolMessage

    requests_by_id: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                call_id = call.get("id")
                if call_id is None:  # pragma: no cover - LangGraph always sets one
                    continue
                requests_by_id[call_id] = {
                    "tool": call["name"],
                    "arguments": call.get("args", {}),
                }

    invocations: list[AgentToolInvocation] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        request = requests_by_id.get(msg.tool_call_id, {"tool": "unknown", "arguments": {}})
        raw_content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        ok = True
        summary = raw_content
        try:
            parsed = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            ok = bool(parsed.get("ok", True))
            summary = parsed.get("error", "") if not ok else json.dumps(
                {k: v for k, v in parsed.items() if k != "ok"}
            )
        invocations.append(
            AgentToolInvocation(
                tool=request["tool"],
                arguments=request["arguments"],
                ok=ok,
                summary=summary[:200],
            )
        )
    return invocations
