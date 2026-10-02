"""LangChain orchestration backend.

Implements the same :class:`~src.domain.interfaces.AnalysisPipeline` port as the
native :class:`~src.services.analysis_service.AnalysisService`, but expresses the
LLM stages as **LCEL** (LangChain Expression Language) runnables:

    selection_chain   = build_messages | chat_model | JsonOutputParser
    explanation_agent = create_agent(chat_model, damage_calc/chaos_meta_stats/
                         smogon_strategy tools, system_prompt)   -- see ADR-028

The deterministic middle (Chaos context + damage/speed calc + Smogon strategy)
is the shared :class:`~src.services.ground_truth.GroundTruthAssembler`, so every
backend yields identical ground-truth numbers. The explanation stage is a bounded tool-calling
agent (ADR-028): it always receives the full precomputed ground truth exactly
like before, but may additionally reach back into the SAME deterministic ports
mid-answer for a question that precomputed context doesn't cover (a
hypothetical item, a different rating tier, ...). This capability is scoped to
this backend only — the native :class:`AnalysisService` has no agent loop.

The prompt-braces problem (the system prompts contain literal ``{ }`` JSON
examples) is avoided by composing messages as concrete objects inside a
``RunnableLambda`` instead of routing them through a templating parser.
Messages are passed as OpenAI-style ``{"role", "content"}`` dicts, which every
LangChain chat model and ``create_agent`` accept natively — so this service
needs no import from the LangChain adapter package. The agent's tools are
built by the composition root and injected (``tools``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Sequence

from src.domain.exceptions import LLMProviderError
from src.domain.interfaces import ConversationMemory, LogParser, PromptRepository
from src.domain.models import (
    AgentToolInvocation,
    AnalysisEvidence,
    AnalysisRequest,
    AnalysisResult,
    ChatMessage,
    GameState,
    SelectionPlan,
)
from src.services.battle_context import candidate_species, outcome_summary, rosters
from src.services.ground_truth import (
    GroundTruthAssembler,
    build_explanation_input,
    build_result,
    remember_turn,
)
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
    from langchain_core.runnables import Runnable

_LcMessage = dict[str, str]


class LangChainAnalysisOrchestrator:
    """AnalysisPipeline implemented with LangChain: a plain LCEL chain for
    selection (1st AI), a bounded tool-calling agent for explanation (2nd AI,
    see ADR-028)."""

    def __init__(
        self,
        *,
        parser: LogParser,
        chat_model: BaseChatModel,
        evidence: GroundTruthAssembler,
        memory: ConversationMemory,
        prompts: PromptRepository,
        tools: Sequence[Any] = (),
        provider_name: str = "langchain",
        max_matchups: int = 6,
        agent_max_steps: int = 20,
    ) -> None:
        self._parser = parser
        self._evidence = evidence
        self._memory = memory
        self._provider_name = provider_name
        self._max_matchups = max_matchups
        # LangGraph "steps" (one model turn or one tool turn each) the
        # explanation agent may take before its own run is cut off — not a
        # count of tool calls directly, but generous enough for a handful of
        # them (see ADR-028). Never applies to the selection stage, which
        # stays a single plain completion. Raised from 10 to 20 (2026-08-29)
        # alongside the ADK backend's equivalent bump — see
        # adk_orchestrator.py's own comment: a live, genuinely complex
        # question (a multi-turn Trick Room comeback explanation) needed
        # more budget than 10 to actually reach an answer.
        self._agent_max_steps = agent_max_steps

        self._selection_system = prompts.get("selection_system")
        explanation_system = (
            f"{prompts.get('explanation_system')}\n\n"
            f"{prompts.get('explanation_agent_addendum')}"
        )
        self._selection_chain = self._build_selection_chain(chat_model)
        self._explanation_agent = self._build_explanation_agent(
            chat_model, list(tools), explanation_system
        )

    def _build_selection_chain(self, chat_model: BaseChatModel) -> Runnable[Any, Any]:
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.runnables import RunnableLambda

        return RunnableLambda(self._selection_messages) | chat_model | JsonOutputParser()

    @staticmethod
    def _build_explanation_agent(
        chat_model: BaseChatModel, tools: list[Any], system_prompt: str
    ) -> Any:
        """Build the explanation stage as a bounded tool-calling agent
        (ADR-028) instead of a bare chat-model completion: the model may
        call damage_calc/chaos_meta_stats/smogon_strategy mid-answer for a
        question the precomputed evidence doesn't already cover. Every tool
        wraps the exact same deterministic ports the evidence stage uses
        (see evidence_tools.py) — never a second, competing source of
        truth, only a second, on-demand way to reach the same one."""
        from langchain.agents import create_agent

        return create_agent(model=chat_model, tools=tools, system_prompt=system_prompt)

    def _selection_messages(self, payload: dict[str, Any]) -> list[_LcMessage]:
        return [
            {"role": "system", "content": self._selection_system},
            *_as_lc_messages(payload["history"]),
            {"role": "user", "content": payload["input"]},
        ]

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        history = self._memory.load(request.session_id)
        game_state = self._evidence.prepare(self._parser, request)
        selection = self._select(request, game_state, history)
        evidence = self._evidence.assemble(
            request=request, game_state=game_state, selection=selection, history=history
        )
        tool_calls: list[AgentToolInvocation] = []

        def explain(correction: str) -> str:
            answer, calls = self._explain(request, history, evidence, correction)
            tool_calls.extend(calls)
            return answer

        answer, warnings = self._evidence.explain_within_regulation(explain, evidence, game_state)
        remember_turn(self._memory, request, answer)
        return build_result(
            request=request,
            evidence=evidence,
            answer=answer,
            provider=self._provider_name,
            agent_tool_calls=tool_calls,
            regulation_warnings=warnings,
        )

    def _explain(
        self,
        request: AnalysisRequest,
        history: Sequence[ChatMessage],
        evidence: AnalysisEvidence,
        correction: str,
    ) -> tuple[str, list[AgentToolInvocation]]:
        # The agent talks to the raw LangChain chat model directly (not
        # through OpenAIProvider/GeminiProvider, which already wrap SDK
        # errors) — this is the one place a provider failure (rate limit,
        # exhausted quota, auth, network) would otherwise reach the UI as a
        # raw, unhandled SDK exception instead of the ProfessorVGCError the
        # presentation layer already knows how to render. A bad/failed tool
        # call inside the loop does NOT reach here — evidence_tools.py
        # degrades those to {"ok": False, "error": ...} instead of raising.
        messages = [
            *_as_lc_messages(history),
            {"role": "user", "content": build_explanation_input(request.question, evidence, correction)},
        ]
        try:
            agent_result = self._explanation_agent.invoke(
                {"messages": messages},
                config={"recursion_limit": self._agent_max_steps},
            )
        except Exception as exc:  # noqa: BLE001 - many SDK exception types
            raise LLMProviderError(
                f"The explanation model call failed ({request.provider}): {exc}"
            ) from exc

        agent_messages = agent_result.get("messages", [])
        final_content = agent_messages[-1].content if agent_messages else ""
        answer = final_content if isinstance(final_content, str) else str(final_content)
        return answer, _extract_tool_invocations(agent_messages)

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


def _as_lc_messages(history: Sequence[ChatMessage]) -> list[_LcMessage]:
    """Domain chat history as role/content dicts (LangChain converts them)."""
    return [{"role": m.role, "content": m.content} for m in history]


def _extract_tool_invocations(messages: Sequence[Any]) -> list[AgentToolInvocation]:
    """Pull every on-demand tool call the explanation agent made this turn out
    of its LangGraph message trace, so the UI can flag it (ADR-028). Matches
    each ToolMessage back to the AIMessage.tool_calls entry that requested it
    (by tool_call_id) and reads the {"ok": ..., ...}/{"ok": False, "error":...}
    shape every tool in evidence_tools.py returns. Returns an empty list for
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
