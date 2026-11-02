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


def _native(chaos_path, request: AnalysisRequest, explanation="native answer") -> AnalysisResult:
    llm = FakeLLM(selection_json=_SELECTION, explanation=explanation)
    return AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(chaos_path), llm=llm, memory=InMemoryConversationMemory(),
        prompts=PROMPTS,
    ).analyze(request)


def _langchain(chaos_path, request: AnalysisRequest, explanation="lc answer") -> AnalysisResult:
    pytest.importorskip("langchain_core")
    from src.adapters.llm.langchain_tools import build_langchain_tools
    from src.services.langchain_orchestrator import LangChainAnalysisOrchestrator
    from tests.test_langchain_orchestrator import _fake_model

    return LangChainAnalysisOrchestrator(
        parser=ShowdownReplayParser(), chat_model=_fake_model(_SELECTION, explanation),
        evidence=build_evidence(chaos_path), memory=InMemoryConversationMemory(),
        prompts=PROMPTS, tools=build_langchain_tools(build_tools(chaos_path)),
    ).analyze(request)


def _adk(chaos_path, request: AnalysisRequest, explanation="adk answer") -> AnalysisResult:
    pytest.importorskip("google.adk")
    from src.adapters.llm.adk_tools import build_adk_tools
    from src.services.adk_orchestrator import AdkAnalysisOrchestrator
    from tests.test_adk_orchestrator import _FakeAdkModel

    return AdkAnalysisOrchestrator(
        parser=ShowdownReplayParser(), model=_FakeAdkModel([_SELECTION, explanation]),
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


def test_all_backends_render_the_same_verified_bo3_claims(sample_chaos_path):
    from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
    from src.domain.series import Bo3AnalysisRequest
    from src.services.bo3_claim_plan import build_claim_plan
    from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
    from src.services.series_analysis import prepare_series
    from tests.test_bo3_claim_plan import draft
    from tests.test_bo3_inference import AnalyticProjection, log, sheet

    series = Bo3AnalysisRequest(session_id="claims-parity", games=[log()], teamsheet=sheet())
    evidence, states = prepare_series(ShowdownReplayParser(), series)
    inferred = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection()).analyze(series, evidence, states)
    plan = build_claim_plan(inferred.checkpoints[-1], "EV spreads")
    request = AnalysisRequest(session_id="claims-parity", replay_raw_text=log(),
        question="EV spreads", series_context={"game": 1}, bo3_claim_plan=plan)
    response = draft(plan)
    results = [factory(sample_chaos_path, request, response) for factory in (_native, _langchain, _adk)]
    assert "Verified teamsheet and spread findings" in results[0].answer
    assert "did not pass" not in results[0].answer
    assert all(result.answer == results[0].answer and result.bo3_claim_plan == plan for result in results)
