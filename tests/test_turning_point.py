"""The decisive turn is computed from the log, never chosen by the model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, GameState
from src.services.analysis_service import AnalysisService
from src.services.ground_truth import parse_replay
from src.services.selection_service import LLMSelectionService
from src.services.turning_point import find_turning_point
from tests.conftest import PROMPTS, FakeLLM, build_evidence

_REPLAYS = Path(__file__).parent / "fixtures" / "replays"


def _replay(replay_id: str) -> dict:
    return json.loads((_REPLAYS / f"gen9championsvgc2026regmb-{replay_id}.json").read_text(encoding="utf-8"))


def _state(replay_id: str) -> GameState:
    return parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="s", replay_json=_replay(replay_id), question="q"))


def _service(chaos_path: Path, llm: FakeLLM) -> AnalysisService:
    return AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(chaos_path), llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS)


@pytest.mark.parametrize(("replay_id", "turn", "faints"), [
    # Triple KO on turn 11: the winner finished ahead and the loser never recovered.
    ("2691404852", 11, {"p1 Sneasler fainted", "p1 Kingambit fainted", "p2 Basculegion fainted"}),
    ("2691414894", 1, {"p1 Kingambit fainted", "p1 Froslass fainted"}),
    ("2691463439", 2, {"p2 Klefki fainted"}),
])
def test_turning_point_on_recorded_games(replay_id: str, turn: int, faints: set[str]) -> None:
    point = find_turning_point(_state(replay_id))
    assert point is not None
    assert (point.turn, point.rule) == (turn, "lead_held_to_the_end")
    chosen = point.candidates[0]
    assert chosen.turn == turn and set(chosen.faints) == faints and chosen.lead >= 0.5
    assert len(point.candidates) == 3 and len({c.turn for c in point.candidates}) == 3


def test_the_winner_is_the_side_that_won_the_game() -> None:
    state = _state("2691404852")
    point = find_turning_point(state)
    assert point is not None and state.outcome is not None
    assert state.outcome.winner_player == point.winner_player == "p2"


def test_no_turning_point_for_forfeits_or_missing_logs() -> None:
    state = _state("2691404852")
    assert state.outcome is not None
    forfeited = state.model_copy(update={"outcome": state.outcome.model_copy(update={"forfeited_player": "p1"})})
    assert find_turning_point(forfeited) is None
    assert find_turning_point(state.model_copy(update={"outcome": None})) is None
    no_winner = state.outcome.model_copy(update={"winner_player": None})
    assert find_turning_point(state.model_copy(update={"outcome": no_winner})) is None


def test_bo1_context_carries_the_turning_point_and_bo3_does_not(sample_chaos_path: Path) -> None:
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "ok")
    service = _service(sample_chaos_path, llm)
    result = service.analyze(AnalysisRequest(session_id="s", replay_json=_replay("2691414894"), question="Which turn decided it?"))
    assert result.turning_point is not None and result.turning_point.turn == 1
    sent = llm.calls[-1]["messages"][-1].content
    assert '"turning_point"' in sent and "led 4 Pokemon to 2" in sent
    bo3 = service.analyze(AnalysisRequest(
        session_id="t", replay_json=_replay("2691414894"), question="q", series_context={"game": 1}))
    assert bo3.turning_point is None


def test_context_omits_the_field_without_a_log(sample_chaos_path: Path, sample_replay_path: Path) -> None:
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "ok")
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = _service(sample_chaos_path, llm).analyze(AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    assert result.turning_point is None and "turning_point" not in llm.calls[-1]["messages"][-1].content
