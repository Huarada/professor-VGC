"""LangChain orchestration backend (LCEL).

    selection_chain   = build_messages | chat_model | JsonOutputParser
    explanation_agent = create_agent(chat_model, evidence tools)  # ADR-028

Ground truth comes from the shared ``GroundTruthAssembler``. Messages are
built as role/content dicts inside a ``RunnableLambda`` because the prompts
contain literal ``{ }`` that a template parser would break on.
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
    # langchain_core is imported lazily so this module loads without it.
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable

_LcMessage = dict[str, str]


class LangChainAnalysisOrchestrator:
    """AnalysisPipeline with an LCEL selection chain and a bounded tool-calling
    explanation agent (ADR-028).
    """

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
        # LangGraph steps for the explanation agent (ADR-028); complex questions
        # needed more than 10.
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
        """The explanation agent: may call the evidence tools (same deterministic
        ports as the evidence stage) for what the precomputed context lacks.
        """
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
        # Wrap raw SDK failures in the typed error the UI renders; tool failures
        # never get here (they return {"ok": False}).
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
    """The tool calls in the agent's message trace, paired by tool_call_id (ADR-028)."""
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
