"""Analysis orchestrator (native backend) — one implementation of the pipeline.

Depends ONLY on domain Protocols and the shared
:class:`~src.services.ground_truth.GroundTruthAssembler`. Satisfies the
:class:`~src.domain.interfaces.AnalysisPipeline` port, so it is interchangeable
with the LangChain and Google ADK orchestrators.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.interfaces import (
    ConversationMemory,
    LLMProvider,
    LogParser,
    PromptRepository,
    SelectionStrategy,
)
from src.domain.models import AnalysisEvidence, AnalysisRequest, AnalysisResult, ChatMessage
from src.services.ground_truth import (
    GroundTruthAssembler,
    build_explanation_input,
    build_result,
    remember_turn,
)


class AnalysisService:
    """Orchestrates parse -> select -> evidence -> explain (native)."""

    def __init__(
        self,
        *,
        parser: LogParser,
        selector: SelectionStrategy,
        evidence: GroundTruthAssembler,
        llm: LLMProvider,
        memory: ConversationMemory,
        prompts: PromptRepository,
    ) -> None:
        self._parser = parser
        self._selector = selector
        self._evidence = evidence
        self._llm = llm
        self._memory = memory
        self._explanation_system = prompts.get("explanation_system")

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Run one full analysis turn and return the UI DTO."""
        history = self._memory.load(request.session_id)
        game_state = self._evidence.prepare(self._parser, request)
        selection = self._selector.select(
            request=request, game_state=game_state, history=history
        )
        evidence = self._evidence.assemble(
            request=request, game_state=game_state, selection=selection, history=history
        )
        answer, warnings = self._evidence.explain_within_regulation(
            lambda correction: self._explain(request, history, evidence, correction),
            evidence, game_state,
        )
        remember_turn(self._memory, request, answer)
        return build_result(
            request=request,
            evidence=evidence,
            answer=answer,
            provider=getattr(self._llm, "name", request.provider),
            regulation_warnings=warnings,
        )

    def _explain(
        self,
        request: AnalysisRequest,
        history: Sequence[ChatMessage],
        evidence: AnalysisEvidence,
        correction: str = "",
    ) -> str:
        user_turn = ChatMessage(
            role="user",
            content=build_explanation_input(request.question, evidence, correction),
        )
        return self._llm.complete(
            system=self._explanation_system,
            messages=[*history, user_turn],
            temperature=0.3,
            json_mode=False,
        )
