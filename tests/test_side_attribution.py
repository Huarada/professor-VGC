"""An answer must not credit a Pokemon to the wrong side."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, GameState, PokemonSet, SideState
from src.services.analysis_service import AnalysisService
from src.services.selection_service import LLMSelectionService
from src.services.side_attribution import side_attribution_issues
from tests.conftest import PROMPTS, FakeLLM, build_evidence

_REPLAY = Path(__file__).parent / "fixtures" / "replays" / "gen9championsvgc2026regmb-2691463439.json"
_SELECTION = json.dumps({"focus_species": [], "matchups": []})


def _state() -> GameState:
    def side(player: str, name: str, species: list[str]) -> SideState:
        return SideState(player=player, player_name=name, team=[PokemonSet(species=s) for s in species], active=species)

    return GameState(sides=[side("p1", "Player A", ["Basculegion", "Dragonite", "Ditto"]),
                            side("p2", "Player B", ["Kingambit", "Froslass", "Ditto"])])


def test_flags_a_switch_between_opposing_teams() -> None:
    issues = side_attribution_issues("Switching **Basculegion** to **Kingambit** would have saved it.", _state())
    assert len(issues) == 1 and "opposing teams" in issues[0]


def test_flags_a_wrong_possessive_and_a_wrong_prefix() -> None:
    assert side_attribution_issues("Player A's Kingambit used Sucker Punch.", _state())
    assert side_attribution_issues("p2 Dragonite fainted.", _state())


def test_accepts_correct_attribution() -> None:
    text = "Player B's **Kingambit** hit p1 Basculegion, so Player A should switch Basculegion to Dragonite."
    assert side_attribution_issues(text, _state()) == []


def test_a_species_on_both_sides_is_never_flagged() -> None:
    assert side_attribution_issues("Player A's Ditto and p2 Ditto, then switch Ditto to Basculegion.", _state()) == []


class _Scripted(FakeLLM):
    """Answers ``first`` until the CORRECTION note arrives, then ``second``."""

    def __init__(self, first: str, second: str) -> None:
        super().__init__(_SELECTION, first)
        self._second = second

    def complete(self, **kwargs: Any) -> str:
        self.calls.append({"system": kwargs["system"], "json_mode": kwargs["json_mode"], "messages": list(kwargs["messages"])})
        if kwargs["json_mode"]:
            return _SELECTION
        corrected = "CORRECTION: your previous answer contradicts" in kwargs["messages"][-1].content
        return self._second if corrected else self._explanation


def _run(chaos_path: Path, llm: FakeLLM) -> Any:
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(chaos_path), llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS)
    replay = json.loads(_REPLAY.read_text(encoding="utf-8"))
    return service.analyze(AnalysisRequest(session_id="s", replay_json=replay, question="q"))


def test_service_retries_once_with_the_correction(sample_chaos_path: Path) -> None:
    wrong = "Switching Diggersby to Klefki on turn 3 would have helped."
    right = "Player A could have kept Diggersby in and protected."
    llm = _Scripted(wrong, right)
    result = _run(sample_chaos_path, llm)
    assert result.answer == right and not any("may contradict the log" in w for w in result.regulation_warnings)
    assert sum(not call["json_mode"] for call in llm.calls) == 2  # one answer, one corrected answer


def test_service_reports_what_remains_and_never_loops(sample_chaos_path: Path) -> None:
    wrong = "Switching Diggersby to Klefki on turn 3 would have helped."
    llm = _Scripted(wrong, wrong)
    result = _run(sample_chaos_path, llm)
    assert result.answer == wrong and any("may contradict the log" in w for w in result.regulation_warnings)
    assert sum(not call["json_mode"] for call in llm.calls) == 2
