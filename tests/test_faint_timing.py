"""A faint must be placed on the turn the log shows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, BattleOutcome, GameState, KOEvent, PokemonSet, SideState
from src.services.analysis_service import AnalysisService
from src.services.faint_timing import faint_timing_issues
from src.services.ground_truth import parse_replay
from src.services.selection_service import LLMSelectionService
from tests.conftest import PROMPTS, FakeLLM, build_evidence

_REPLAYS = Path(__file__).parent / "fixtures" / "replays"
_SELECTION = json.dumps({"focus_species": [], "matchups": []})


def _state() -> GameState:
    def side(player: str, name: str, species: list[str]) -> SideState:
        return SideState(player=player, player_name=name, team=[PokemonSet(species=s) for s in species], active=species)

    return GameState(
        sides=[side("p1", "Player A", ["Dragonite", "Mr. Rime", "Ditto"]), side("p2", "Player B", ["Kingambit", "Ditto"])],
        outcome=BattleOutcome(winner_player="p2", turns=12, kos=[
            KOEvent(turn=11, fainted="Kingambit", player="p2"), KOEvent(turn=12, fainted="Dragonite", player="p1"),
            KOEvent(turn=5, fainted="Mr. Rime", player="p1"), KOEvent(turn=3, fainted="Ditto", player="p1"),
            KOEvent(turn=9, fainted="Ditto", player="p2")]))


def test_flags_a_faint_placed_on_the_wrong_turn() -> None:
    text = "On turn 11, Player B's Kingambit used Sucker Punch, resulting in a KO of Player A's Dragonite."
    issues = faint_timing_issues(text, _state())
    assert len(issues) == 1 and "Dragonite fainted on turn 12" in issues[0]


def test_flags_the_victim_before_the_verb_and_a_pokemon_that_never_fainted() -> None:
    assert faint_timing_issues("Dragonite fainted on Turn 11.", _state())
    issues = faint_timing_issues("Turn 4 ended when Player B's Kingambit fainted, then Mr. Rime fainted.", _state())
    assert any("Kingambit fainted on turn 11" in issue for issue in issues)


def test_a_pokemon_that_never_fainted_is_said_to_have_never_fainted() -> None:
    state = _state()
    state.sides[0].team.append(PokemonSet(species="Archaludon"))
    state.sides[0].active.append("Archaludon")  # brought
    (issue,) = faint_timing_issues("On turn 8, Player A's Archaludon fainted.", state)
    assert "Archaludon never fainted in this game" in issue


def test_accepts_correct_turns_and_names_with_a_period() -> None:
    assert faint_timing_issues("On turn 12 Player A's Dragonite fainted to Sucker Punch.", _state()) == []
    assert faint_timing_issues("Mr. Rime fainted on turn 5.", _state()) == []


def test_ignores_counterfactuals_unclear_claims_and_several_turns() -> None:
    state = _state()
    assert faint_timing_issues("On turn 4, Wave Crash would have KO'd Kingambit.", state) == []
    assert faint_timing_issues("On turn 4, Kingambit had a guaranteed KO on Dragonite.", state) == []
    assert faint_timing_issues("By turns 5 and 11 Dragonite fainted.", state) == []
    assert faint_timing_issues("Dragonite fainted late in the game.", state) == []


def test_a_species_on_both_sides_matches_either_players_faint_unless_the_text_names_one() -> None:
    state = _state()
    assert faint_timing_issues("Ditto fainted on turn 9.", state) == []
    assert faint_timing_issues("Player A's Ditto fainted on turn 9.", state)
    assert faint_timing_issues("Player A's Ditto fainted on turn 3.", state) == []


def test_recorded_game_faints_from_the_log_are_accepted() -> None:
    replay = json.loads((_REPLAYS / "gen9championsvgc2026regmb-2691404852.json").read_text(encoding="utf-8"))
    state = parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    assert faint_timing_issues("On turn 11 Player A's Sneasler fainted, and on turn 12 Dragonite fainted.", state) == []
    assert faint_timing_issues("On turn 11 Player A lost Sneasler and Kingambit.", state) == []
    assert faint_timing_issues("The KO of Player A's Dragonite on turn 11 decided it.", state)


def test_service_corrects_a_misplaced_faint_once(sample_chaos_path: Path) -> None:
    replay = json.loads((_REPLAYS / "gen9championsvgc2026regmb-2691404852.json").read_text(encoding="utf-8"))
    wrong = "On turn 11, Player B's Kingambit used Sucker Punch for a KO of Player A's Dragonite."
    right = "On turn 12, Player B's Kingambit used Sucker Punch and Player A's Dragonite fainted."

    class Scripted(FakeLLM):
        def complete(self, **kwargs: Any) -> str:
            self.calls.append({"system": kwargs["system"], "json_mode": kwargs["json_mode"], "messages": list(kwargs["messages"])})
            if kwargs["json_mode"]:
                return _SELECTION
            return right if "CORRECTION: your previous answer contradicts" in kwargs["messages"][-1].content else wrong

    llm = Scripted(_SELECTION, wrong)
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=build_evidence(sample_chaos_path), llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS)
    result = service.analyze(AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    assert result.answer == right and not any("contradict" in w for w in result.regulation_warnings)
    corrected = llm.calls[-1]["messages"][-1].content
    assert "Dragonite fainted on turn 12" in corrected


def test_a_mega_forme_shares_its_base_pokemons_faint() -> None:
    replay = json.loads((_REPLAYS / "gen9championsvgc2026regmb-2691463439.json").read_text(encoding="utf-8"))
    state = parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    assert faint_timing_issues("On turn 4 Player A knocked out Player B's Altaria-Mega.", state) == []
    assert faint_timing_issues("On turn 3 Player A knocked out Player B's Altaria-Mega.", state)


def test_this_turn_continues_the_turn_named_by_the_previous_sentence() -> None:
    state = _state()
    wrong = "The turning point was turn 11. During this turn, Player B's Kingambit knocked out Player A's Dragonite."
    assert faint_timing_issues(wrong, state)
    right = "The turning point was turn 12. During this turn, Player B's Kingambit knocked out Player A's Dragonite."
    assert faint_timing_issues(right, state) == []
    assert faint_timing_issues("Dragonite fainted during this turn.", state) == []  # no turn named before it


def test_recognises_the_common_ways_of_saying_a_pokemon_was_knocked_out() -> None:
    state = _state()
    for phrase in ("Kingambit used Sucker Punch to knock out Player A's Dragonite", "Kingambit knocks out Dragonite",
                   "Kingambit took out Player A's Dragonite", "Kingambit KO'd Dragonite", "Kingambit defeated Dragonite"):
        assert faint_timing_issues(f"On turn 11, {phrase}.", state), phrase
        assert faint_timing_issues(f"On turn 12, {phrase}.", state) == [], phrase


def test_a_counterfactual_word_in_another_clause_does_not_hide_a_wrong_faint() -> None:
    """gpt-4o-mini: "(Turn 2): ... it fainted Rotom-Fan, significantly decreasing ... offensive potential" (it fainted on 3)."""
    from src.services.faint_timing import clause_around

    sentence = "since it fainted Rotom-Fan, significantly decreasing Player B's offensive potential"
    start = sentence.index("fainted")
    assert clause_around(sentence, start, start + len("fainted Rotom-Fan")) == "since it fainted Rotom-Fan"
    other = "If it had protected, X would have fainted"
    assert clause_around(other, other.index("X"), len(other)) == " X would have fainted"
