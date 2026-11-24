"""The answer must present the engine's turn as the turning point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, GameState
from src.services.analysis_service import AnalysisService
from src.services.ground_truth import parse_replay
from src.services.selection_service import LLMSelectionService
from src.services.turning_claim import claimed_turning_turns, turning_point_issues
from tests.conftest import PROMPTS, FakeLLM, build_evidence

_REPLAY = Path(__file__).parent / "fixtures" / "replays" / "gen9championsvgc2026regmb-2691404852.json"  # engine turn: 11
_SELECTION = json.dumps({"focus_species": [], "matchups": []})


def _state() -> GameState:
    replay = json.loads(_REPLAY.read_text(encoding="utf-8"))
    return parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="s", replay_json=replay, question="q"))


def test_a_different_turn_called_the_turning_point_is_flagged() -> None:
    issues = turning_point_issues("The turning point of the game was turn 12, when Dragonite fainted.", _state())
    assert len(issues) == 1 and "turn 12" in issues[0] and "turn 11" in issues[0]


def test_the_engines_turn_passes_in_every_phrasing() -> None:
    state = _state()
    for text in ("The turning point occurred on **Turn 11**.", "The decisive moment came on turn 11.",
                 "### Turning Point\nOn turn 11 the lead became permanent.", "Turn 11 was pivotal for Player B.",
                 "The turning point was turn 11, and a secondary swing came on turn 4."):
        assert turning_point_issues(text, state) == [], text


def test_a_secondary_swing_is_allowed_but_not_instead_of_the_engines_turn() -> None:
    state = _state()
    assert turning_point_issues("The turning point was turn 11. Another decisive moment came on turn 4.", state) == []
    assert turning_point_issues("The decisive moment came on turn 4, and turn 11 only finished it.", state)


def test_no_claim_no_flag_and_no_flag_without_an_engine_turn() -> None:
    state = _state()
    assert turning_point_issues("Player B won because Kingambit survived on turn 7.", state) == []
    assert turning_point_issues("The turning point was turn 5.", state.model_copy(update={"outcome": None})) == []
    assert claimed_turning_turns("### Turning Point\nSomething happened.", []) == []


def test_service_asks_for_one_correction_when_another_turn_is_called_decisive() -> None:
    wrong = "### Turning Point\nThe turning point of the game was turn 12."
    right = "### Turning Point\nThe turning point of the game was turn 11."

    class Scripted(FakeLLM):
        def complete(self, **kwargs: Any) -> str:
            self.calls.append({"system": kwargs["system"], "json_mode": kwargs["json_mode"], "messages": list(kwargs["messages"])})
            if kwargs["json_mode"]:
                return _SELECTION
            return right if "CORRECTION: your previous answer contradicts" in kwargs["messages"][-1].content else wrong

    llm = Scripted(_SELECTION, wrong)
    chaos = Path(__file__).parents[1] / "sample_data" / "gen9championsvgc2026regmb.json"
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(chaos), llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS)
    replay = json.loads(_REPLAY.read_text(encoding="utf-8"))
    result = service.analyze(AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    assert result.answer == right and not any("contradict" in w for w in result.regulation_warnings)
    assert "the engine computed turn 11" in llm.calls[-1]["messages"][-1].content
