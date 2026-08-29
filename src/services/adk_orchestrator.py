"""Google ADK (Agent Development Kit) orchestration backend.

Implements the same :class:`~src.domain.interfaces.AnalysisPipeline` port as
:class:`~src.services.analysis_service.AnalysisService` (native) and
:class:`~src.services.langchain_orchestrator.LangChainAnalysisOrchestrator`
(LangChain), so it is a fully interchangeable third
``PROFESSORVGC_ORCHESTRATOR`` value (``adk`` — the default). Same shape as
the LangChain backend: a plain, tool-less ``LlmAgent`` for selection (1st
AI), a tool-calling ``LlmAgent`` for explanation (2nd AI) wired to the exact
same deterministic ports (:func:`~src.adapters.llm.adk_tools.build_adk_tools`
mirrors ``langchain_tools.py``'s three tools verbatim) — so a result from
this backend is exactly as trustworthy as any other, and switching
orchestration technology never changes a single damage roll.

Two deliberate simplifications relative to full ADK idiom, both documented
here rather than left implicit:

- **No native ADK session-history replay.** ADK's ``Session``/``Event``
  mechanism is built to carry a conversation's history internally across
  ``Runner.run_async`` calls sharing one ``session_id`` — but this project
  already has its own cross-backend memory abstraction
  (:class:`~src.domain.interfaces.ConversationMemory`, loaded/appended by
  every backend identically) and every OTHER backend (native, LangChain)
  surfaces prior turns by rendering them into the prompt text rather than
  relying on a framework's own session store. Matching that shape here
  (:func:`_render_history`) keeps memory behavior identical across all
  three backends and avoids depending on exactly how ADK's session/event
  replay interacts with an agent that wasn't the one that produced the
  earlier turns. Each ``analyze()`` call therefore uses a brand-new,
  disposable ADK session (a fresh UUID) purely as the plumbing ADK's
  ``Runner`` requires to execute at all.
- **Selection JSON via ``output_schema``, not a downstream parser.** Unlike
  the LangChain backend (``JsonOutputParser``) or the native one (a
  provider-level ``json_mode`` flag), ADK's ``LlmAgent`` can constrain its
  OWN final output to a schema (ADK docs: "supports using ``output_schema``
  and ``tools`` together... enforcing structure only on the final output").
  The result is still routed through the exact same
  ``src.services.selection_logic`` parse/sanitize pipeline every other
  backend uses (so a same-shaped fallback still applies if a given
  model/backend combination doesn't honor it), just with a stronger
  starting guarantee.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any, Sequence

from pydantic import BaseModel, Field

from src.adapters.llm.adk_tools import build_adk_tools
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
    # Type-checking only — kept lazy at runtime (see the local imports in
    # __init__/_run_agent below) so this module stays importable without
    # google-adk installed, matching langchain_orchestrator.py's own pattern
    # for langchain_core.
    from google.adk.models import BaseLlm

_APP_NAME = "professorvgc"
_USER_ID = "professorvgc-user"


class _SelectionOutput(BaseModel):
    """JSON shape requested from the selection agent via ``output_schema``.

    Deliberately ``list[list[str]]`` rather than ``list[tuple[str, str]]``
    for ``matchups`` — a tuple has no native JSON-Schema representation, and
    ``selection_logic.plan_from_payload`` already accepts either a list or a
    tuple pair, so no downstream change is needed.
    """

    focus_species: list[str] = Field(default_factory=list)
    matchups: list[list[str]] = Field(default_factory=list)
    rationale: str = ""


def _render_history(history: Sequence[ChatMessage]) -> str:
    """Render prior turns as plain text (see module docstring for why)."""
    if not history:
        return ""
    lines = ["Conversation so far:"]
    for message in history:
        speaker = {"user": "User", "assistant": "Assistant"}.get(message.role, message.role)
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines) + "\n\n"


def _final_text(event: Any) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(part.text for part in event.content.parts if getattr(part, "text", None))


def _extract_tool_invocations(events: Sequence[Any]) -> list[AgentToolInvocation]:
    """Pull every on-demand tool call the explanation agent made this turn out
    of its ADK event trace, so the UI can flag it (mirrors ADR-028's LangChain
    equivalent, ``langchain_orchestrator._extract_tool_invocations``).

    Matches each function response back to its request by
    ``FunctionCall.id``/``FunctionResponse.id`` when the model backend sets
    them; falls back to pairing the oldest still-unmatched call of the same
    tool name otherwise (defensive — LangChain's ``tool_call_id`` is always
    present, ADK's ``id`` is documented as optional)."""
    calls_by_id: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for event in events:
        for call in event.get_function_calls():
            entry = {"tool": call.name, "arguments": dict(call.args or {})}
            call_id = getattr(call, "id", None)
            if call_id:
                calls_by_id[call_id] = entry
            else:
                unmatched.append(entry)

    invocations: list[AgentToolInvocation] = []
    for event in events:
        for response in event.get_function_responses():
            response_id = getattr(response, "id", None)
            request = calls_by_id.pop(response_id, None) if response_id else None
            if request is None:
                request = next((e for e in unmatched if e["tool"] == response.name), None)
                if request is not None:
                    unmatched.remove(request)
            if request is None:
                request = {"tool": response.name or "unknown", "arguments": {}}

            payload: dict[str, Any] = dict(response.response or {})
            # Some ADK/LiteLLM paths wrap a plain-dict tool return as
            # {"result": {...}} — unwrap one level so `ok`/`error` are read
            # from the actual tool payload either way.
            if set(payload.keys()) == {"result"} and isinstance(payload["result"], dict):
                payload = payload["result"]
            ok = bool(payload.get("ok", True))
            summary = (
                str(payload.get("error", ""))
                if not ok
                else json.dumps({k: v for k, v in payload.items() if k != "ok"})
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


class AdkAnalysisOrchestrator:
    """AnalysisPipeline implemented with Google ADK: a schema-constrained,
    tool-less ``LlmAgent`` for selection (1st AI), a bounded tool-calling
    ``LlmAgent`` for explanation (2nd AI)."""

    def __init__(
        self,
        *,
        parser: LogParser,
        model: "str | BaseLlm",
        meta_provider: MetaStatsProvider,
        calc_engine: CalcEngineAdapter,
        strategy_provider: StrategyKnowledgeProvider,
        memory: ConversationMemory,
        default_gen: int = 9,
        provider_name: str = "adk",
        max_matchups: int = 6,
        suggestion_source: SmogonSuggestionSource | None = None,
        agent_max_llm_calls: int = 20,
        agent_timeout_seconds: float = 180.0,
    ) -> None:
        from google.adk.agents import Agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self._parser = parser
        self._meta = meta_provider
        self._strategy = strategy_provider
        self._memory = memory
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)
        self._simulator = TurnReplaySimulator(calc_engine, default_gen)
        self._suggestion_source = suggestion_source
        self._provider_name = provider_name
        self._max_matchups = max_matchups
        # Mirrors LangChainAnalysisOrchestrator's `agent_max_steps`: a
        # generous but finite bound on the explanation agent's own
        # think/call-tool/think loop (see RunConfig.max_llm_calls), never
        # applied to the selection stage in spirit (it has no tools to loop
        # on) even though the same RunConfig is reused there for simplicity.
        # Raised from 10 to 20 (2026-08-29) after a live failure: a
        # genuinely complex, open-ended question (explaining a multi-turn
        # Trick Room comeback) exhausted a budget of 10 mid-loop — see
        # _run_agent's own comment for what that used to do (silently
        # return an empty answer, no error at all) before that was also
        # fixed to fail loud instead.
        self._agent_max_llm_calls = agent_max_llm_calls
        # Observed live (2026-08-28, faithfulness benchmark against Gemini
        # 3.5-flash): one agent turn stalled 30+ minutes with no exception
        # ever raised — not a retriable error (adk_provider.py's own
        # retry_options bound already handles those), but a long
        # think/call-tool/think loop that just never returned. Unbounded,
        # that turns into a Streamlit request stuck until Cloud Run's own
        # (much longer, and less informative to the user) request timeout
        # kills the connection. `_run_agent_bounded` wraps every ADK turn in
        # `asyncio.wait_for` so a stuck turn surfaces as a clear, typed
        # error within this budget instead — see that method's own
        # docstring for why true coroutine cancellation is used here rather
        # than the thread-based, best-effort approach the benchmark script
        # (scripts/faithfulness_benchmark/run.py) had to fall back on.
        self._agent_timeout_seconds = agent_timeout_seconds

        self._session_service = InMemorySessionService()

        self._selection_system = load_prompt("selection_system")
        self._explanation_system = load_prompt("explanation_system")

        self._selection_agent = Agent(
            name="professorvgc_selection",
            model=model,
            instruction=self._selection_system,
            output_schema=_SelectionOutput,
        )
        self._selection_runner = Runner(
            agent=self._selection_agent,
            app_name=_APP_NAME,
            session_service=self._session_service,
        )

        tools = build_adk_tools(
            calc_engine=calc_engine,
            meta_provider=meta_provider,
            strategy_provider=strategy_provider,
            default_gen=default_gen,
        )
        explanation_instruction = (
            f"{self._explanation_system}\n\n{load_prompt('explanation_agent_addendum')}"
        )
        self._explanation_agent = Agent(
            name="professorvgc_explanation",
            model=model,
            instruction=explanation_instruction,
            tools=tools,
        )
        self._explanation_runner = Runner(
            agent=self._explanation_agent,
            app_name=_APP_NAME,
            session_service=self._session_service,
        )

    async def _run_agent(self, runner: Any, message_text: str) -> tuple[str, list[Any]]:
        """Run one ADK agent turn to completion on a fresh, disposable
        session (see module docstring) and return its final text plus the
        full event trace (for tool-call extraction)."""
        from google.adk.agents.run_config import RunConfig
        from google.genai import types

        session_id = uuid.uuid4().hex
        await self._session_service.create_session(
            app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id
        )
        content = types.Content(role="user", parts=[types.Part(text=message_text)])
        run_config = RunConfig(max_llm_calls=self._agent_max_llm_calls)

        events: list[Any] = []
        final_text = ""
        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session_id,
            new_message=content,
            run_config=run_config,
        ):
            events.append(event)
            if event.is_final_response():
                text = _final_text(event)
                if text:
                    final_text = text
        if not final_text:
            # Live bug (2026-08-29): a genuinely complex question (a
            # multi-turn Trick Room comeback explanation) exhausted
            # `run_config.max_llm_calls` mid tool-calling loop, so
            # `run_async` simply stopped yielding events — no exception,
            # no timeout (each individual LLM call was fast; it was the
            # CUMULATIVE call count that ran out) — and this returned an
            # empty string with no error at all. The UI rendered a blank
            # answer with no indication anything had gone wrong. The
            # LangChain backend already fails LOUD in the equivalent case
            # (LangGraph's own recursion_limit raises GraphRecursionError,
            # caught by analyze()'s except clause) — this makes the ADK
            # backend match that instead of the silent-empty-string path.
            raise LLMProviderError(
                f"The agent turn ended without producing a final answer after "
                f"{len(events)} events (run_config.max_llm_calls="
                f"{self._agent_max_llm_calls}) — likely a question complex "
                "enough that the agent's tool-calling budget ran out before "
                "it synthesized a response, not a single failed call. Try "
                "asking a narrower question, or increase agent_max_llm_calls."
            )
        return final_text, events

    def _run_agent_bounded(self, runner: Any, message_text: str) -> tuple[str, list[Any]]:
        """Synchronous entry point for one ADK agent turn, with a
        wall-clock ceiling (see ``__init__``'s ``agent_timeout_seconds``
        comment for why this exists).

        ``asyncio.wait_for`` — not a thread/executor with a timeout — on
        purpose: it cancels the underlying coroutine for real (a
        ``CancelledError`` is thrown into it at its current await point,
        which the ADK/genai async HTTP client propagates into actually
        aborting the in-flight request) rather than abandoning a thread
        that keeps running and consuming quota in the background. That
        matters here specifically because, unlike the benchmark script's
        own timeout (which had to isolate each attempt in its own
        throwaway ``Container``/subprocess to make an abandoned thread
        safe), every call here shares this orchestrator's long-lived
        ``Runner``/``InMemorySessionService`` — a leaked thread could still
        be mutating that shared state after this method returns.
        ``asyncio.run`` gives ``wait_for`` a fresh event loop per call,
        matching every other call site in this class.
        """
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._run_agent(runner, message_text),
                    timeout=self._agent_timeout_seconds,
                )
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise LLMProviderError(
                f"The agent turn exceeded its {self._agent_timeout_seconds:.0f}s "
                "budget (no further response arrived — likely a stuck "
                "tool-calling loop or a slow provider, not a single failed "
                "call). Try again; if it keeps happening, the provider may "
                "be degraded."
            ) from exc

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
        message_text = _render_history(history) + build_explanation_input(
            request.question, context
        )
        # Same rationale as ADR-011 (LangChain backend): the agent talks to
        # the raw ADK/model SDK directly, so a provider failure (rate limit,
        # exhausted quota, auth, network) is wrapped here into the typed
        # error the presentation layer already knows how to render, instead
        # of reaching the UI as a raw SDK exception. A bad/failed TOOL call
        # inside the loop does NOT reach here — adk_tools.py degrades those
        # to {"ok": False, "error": ...} instead of raising.
        try:
            answer, events = self._run_agent_bounded(self._explanation_runner, message_text)
        except LLMProviderError:
            raise  # already the typed error _run_agent_bounded raises on timeout — don't re-wrap it
        except Exception as exc:  # noqa: BLE001 - many SDK exception types
            raise LLMProviderError(
                f"The explanation model call failed ({request.provider}): {exc}"
            ) from exc
        agent_tool_calls = _extract_tool_invocations(events)

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
        message_text = _render_history(history) + build_selection_input(
            game_state.format_id,
            rosters(game_state),
            request.question,
            outcome_summary(game_state),
        )
        try:
            raw, _events = self._run_agent_bounded(self._selection_runner, message_text)
        except Exception:  # noqa: BLE001 - model failure (including a timeout) -> deterministic fallback
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
