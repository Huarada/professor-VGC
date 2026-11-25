"""BO1 calls carry only the BO1 explanation rules; BO3 game reviews append the series rules."""

from __future__ import annotations

import json

from src.adapters.llm.prompts import FilePromptRepository
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest
from src.services.analysis_service import AnalysisService
from src.services.ground_truth import explanation_system_prompt
from src.services.selection_service import LLMSelectionService
from tests.conftest import PROMPTS, FakeLLM, build_evidence

_BO3_ONLY = ("BO3 VERIFIED CLAIM CONTRACT", "verified_claim_plan", "set_inference")
_prompts = FilePromptRepository()


def test_bo1_prompt_has_no_series_rules_and_bo3_prompt_keeps_all_of_them() -> None:
    bo1 = explanation_system_prompt(_prompts, series=False)
    bo3 = explanation_system_prompt(_prompts, series=True)
    assert not any(marker in bo1 for marker in _BO3_ONLY)
    assert all(marker in bo3 for marker in _BO3_ONLY)
    shared = _prompts.get("explanation_system")
    assert bo1.startswith(shared) and bo3.startswith(shared) and len(bo3) > len(bo1) + 1000
    # The BO1 answer structure must never compete with the BO3 JSON contract (it made gpt-4o-mini answer in prose).
    assert "BO1 ANSWER STRUCTURE" in bo1 and "BO1 ANSWER STRUCTURE" not in bo3 and "Answer structure" not in bo3


def test_bo1_prompt_structure_regulation_example_and_callback_order() -> None:
    text = explanation_system_prompt(_prompts, series=False)
    assert "Reg M-B" not in text  # the target regulation moves; examples stay generic
    for section in ("1. Result:", "2. Turning point:", "3. Decisions:", "4. What to change:"):
        assert section in text
    assert text.index("REGULATION") < text.index("RECURRING CONCEPTS") < text.index("Absolute rules")
    assert text.index("Predictive / risk-reward analysis (go beyond") < text.index("BO1 ANSWER STRUCTURE")


def test_native_service_picks_the_prompt_per_request(sample_chaos_path, sample_replay_path) -> None:
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "ok")
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(sample_chaos_path), llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS,
    )
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    service.analyze(AnalysisRequest(session_id="bo1", replay_json=replay, question="q"))
    explanation = llm.calls[1]["system"]  # calls: [selection, explanation]
    assert "BO3 VERIFIED CLAIM CONTRACT" not in explanation and "BO1 ANSWER STRUCTURE" in explanation
