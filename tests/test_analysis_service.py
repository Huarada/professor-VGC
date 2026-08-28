"""End-to-end orchestration test with fakes (no Node, no API keys)."""

from __future__ import annotations

import json

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.models import AnalysisRequest
from src.services.analysis_service import AnalysisService
from src.services.selection_service import LLMSelectionService
from tests.conftest import FakeCalcEngine, FakeLLM


def _build(chaos_path, llm) -> AnalysisService:
    return AnalysisService(
        parser=ShowdownReplayParser(),
        selector=LLMSelectionService(llm),
        meta_provider=ChaosAdapter(chaos_path),
        calc_engine=FakeCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(chaos_path),
        llm=llm,
        memory=InMemoryConversationMemory(),
    )


def test_full_pipeline(sample_chaos_path, sample_replay_path):
    llm = FakeLLM(
        selection_json=json.dumps(
            {"focus_species": ["Garchomp", "Sinistcha"],
             "matchups": [["Garchomp", "Sinistcha"]], "rationale": "test"}
        ),
        explanation="Garchomp threatens a 2HKO; Sinistcha is the safe pivot.",
    )
    service = _build(sample_chaos_path, llm)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = service.analyze(
        AnalysisRequest(session_id="game-1", replay_json=replay,
                        question="Does Garchomp beat Sinistcha?")
    )
    assert result.answer.startswith("Garchomp")
    assert result.verdicts
    v = result.verdicts[0]
    assert v.attacker == "Garchomp" and v.defender == "Sinistcha"
    assert v.best_move in {"Earthquake", "Dragon Claw", "Rock Slide", "Protect"}
    assert "Garchomp" in result.meta_context.pokemon_stats
    assert result.provider == "fake"


def test_memory_accumulates(sample_chaos_path, sample_replay_path):
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "ok")
    memory = InMemoryConversationMemory()
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm),
        meta_provider=ChaosAdapter(sample_chaos_path), calc_engine=FakeCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(sample_chaos_path), llm=llm, memory=memory,
    )
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    service.analyze(AnalysisRequest(session_id="s9", replay_json=replay, question="q1"))
    service.analyze(AnalysisRequest(session_id="s9", replay_json=replay, question="q2"))
    assert len(memory.load("s9")) == 4


def test_recurring_concept_reaches_the_explanation_prompt_on_a_later_turn(
    sample_chaos_path, sample_replay_path
):
    """End-to-end: a second question that repeats an earlier turn's VGC
    topic must carry a recurring_concepts entry into the explanation
    stage's own user-turn text — this is the actual wiring a demo would
    show, not just the pure concept_tracking.py functions in isolation."""
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "ok")
    service = _build(sample_chaos_path, llm)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))

    service.analyze(AnalysisRequest(
        session_id="concepts-1", replay_json=replay,
        question="How does Trick Room flip the turn order here?",
    ))
    service.analyze(AnalysisRequest(
        session_id="concepts-1", replay_json=replay,
        question="Right, and was Trick Room still up on the turn Garchomp fainted?",
    ))

    # calls: [selection#1, explanation#1, selection#2, explanation#2]
    explanation_call_2 = llm.calls[3]
    assert explanation_call_2["json_mode"] is False
    user_turn_text = explanation_call_2["messages"][-1].content
    assert '"recurring_concepts"' in user_turn_text
    assert "Trick Room" in user_turn_text
    assert "How does Trick Room flip the turn order here?" in user_turn_text


def test_meta_context_covers_all_inplay_not_just_focus(sample_chaos_path, sample_replay_path):
    """Meta/strategy context must cover every in-play Pokemon, not only the focus."""
    llm = FakeLLM(
        selection_json=json.dumps(
            {"focus_species": ["Garchomp"],  # deliberately narrow focus
             "matchups": [["Garchomp", "Sinistcha"]], "rationale": "narrow"}
        ),
        explanation="ok",
    )
    service = _build(sample_chaos_path, llm)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = service.analyze(
        AnalysisRequest(session_id="ctx", replay_json=replay, question="q")
    )
    # Both sides' in-play Pokemon are present even though focus was just Garchomp.
    assert "Garchomp" in result.meta_context.pokemon_stats
    assert "Sinistcha" in result.meta_context.pokemon_stats
    strat_species = {s.species for s in result.strategies}
    assert {"Garchomp", "Sinistcha"} <= strat_species
