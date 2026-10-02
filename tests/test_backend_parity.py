"""Backend parity: the three orchestrators produce IDENTICAL ground truth.

The deterministic evidence stage used to be copy-pasted into each of the
native, LangChain and Google ADK orchestrators, so a new piece of evidence had
to be added three times and a miss silently broke parity. It is now the one
shared ``GroundTruthAssembler``; this test pins that guarantee: same replay,
same selection, same fakes -> the same verdicts, per-turn checks, Protect
reads, meta context, strategies and battle result, whatever the backend.
"""

from __future__ import annotations

import json

import pytest

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, AnalysisResult
from src.services.analysis_service import AnalysisService
from src.services.selection_service import LLMSelectionService
from tests.conftest import PROMPTS, FakeLLM, build_evidence, build_tools
from tests.test_decision_review import _LOG_ATTACKED

_SELECTION = json.dumps(
    {"focus_species": ["Garchomp", "Talonflame"], "matchups": [["Garchomp", "Talonflame"]],
     "rationale": "parity"}
)
_EVIDENCE_FIELDS = (
    "selection", "meta_context", "verdicts", "turn_checks", "protect_reads",
    "strategies", "battle_result",
)


def _native(chaos_path, request: AnalysisRequest) -> AnalysisResult:
    llm = FakeLLM(selection_json=_SELECTION, explanation="native answer")
    return AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(chaos_path), llm=llm, memory=InMemoryConversationMemory(),
        prompts=PROMPTS,
    ).analyze(request)


def _langchain(chaos_path, request: AnalysisRequest) -> AnalysisResult:
    pytest.importorskip("langchain_core")
    from src.adapters.llm.langchain_tools import build_langchain_tools
    from src.services.langchain_orchestrator import LangChainAnalysisOrchestrator
    from tests.test_langchain_orchestrator import _fake_model

    return LangChainAnalysisOrchestrator(
        parser=ShowdownReplayParser(), chat_model=_fake_model(_SELECTION, "lc answer"),
        evidence=build_evidence(chaos_path), memory=InMemoryConversationMemory(),
        prompts=PROMPTS, tools=build_langchain_tools(build_tools(chaos_path)),
    ).analyze(request)


def _adk(chaos_path, request: AnalysisRequest) -> AnalysisResult:
    pytest.importorskip("google.adk")
    from src.adapters.llm.adk_tools import build_adk_tools
    from src.services.adk_orchestrator import AdkAnalysisOrchestrator
    from tests.test_adk_orchestrator import _FakeAdkModel

    return AdkAnalysisOrchestrator(
        parser=ShowdownReplayParser(), model=_FakeAdkModel([_SELECTION, "adk answer"]),
        evidence=build_evidence(chaos_path), memory=InMemoryConversationMemory(),
        prompts=PROMPTS, tools=build_adk_tools(build_tools(chaos_path)),
    ).analyze(request)


def test_all_backends_produce_identical_evidence(sample_chaos_path):
    request = AnalysisRequest(
        session_id="parity", replay_raw_text=_LOG_ATTACKED,
        question="Should Talonflame have protected on turn 3?",
    )
    native = _native(sample_chaos_path, request)
    assert native.turn_checks and native.verdicts and native.battle_result  # meaningful
    assert any(check.incoming_threats for check in native.turn_checks)
    for other in (_langchain(sample_chaos_path, request), _adk(sample_chaos_path, request)):
        for name in _EVIDENCE_FIELDS:
            assert getattr(other, name) == getattr(native, name), name
