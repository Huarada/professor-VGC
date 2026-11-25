"""BO1 evidence names the owner of every Pokemon, lists wasted actions and checks "who used what on turn N" (ADR-082).

The fixture is a near-mirror matchup (Basculegion, Froslass, Kingambit and Sneasler on both sides), the case where a
model credited Player A's failed Sucker Punch to Player B's Kingambit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest, GameState, SelectionPlan
from src.services.action_claims import action_claim_issues
from src.services.battle_context import outcome_summary
from src.services.damage_wording import remaining_hp_as_damage_issues
from src.services.faint_timing import faint_timing_issues
from src.services.ground_truth import build_explanation_context, parse_replay
from src.services.side_attribution import narration_issues
from src.services.side_labels import label_sides, label_text, side_names
from src.services.wasted_actions import find_wasted_actions
from tests.conftest import build_evidence

_REPLAY = Path(__file__).parent / "fixtures" / "replays" / "gen9championsvgc2026regmb-2691404852.json"


@pytest.fixture(scope="module")
def state() -> GameState:
    return ShowdownReplayParser().parse(json.loads(_REPLAY.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def names(state: GameState) -> tuple[str, str]:
    labels = side_names(state)
    return labels["p1"], labels["p2"]


def test_label_text_and_fields_use_the_player_name() -> None:
    names = {"p1": "Ana", "p2": "Bob"}
    assert label_text("p1 Kingambit used Protect", names) == "Ana's Kingambit used Protect"
    assert label_text("p2's side: Aurora Veil started.", names) == "Bob's side: Aurora Veil started."
    assert label_text("[Tailwind p1; weather Snow]", names) == "[Tailwind Ana; weather Snow]"
    assert label_sides({"actor_player": "p1", "rows": [{"target_player": "p2", "note": "p2 Froslass fainted"}], "turn": 3},
                       names) == {"actor_player": "Ana", "rows": [{"target_player": "Bob", "note": "Bob's Froslass fainted"}],
                                  "turn": 3}


def test_named_timeline_has_owners_and_remaining_hp_wording(state: GameState, names: tuple[str, str]) -> None:
    text = outcome_summary(state, named_sides=True)
    one, two = names
    assert f"{one}'s Sneasler used Fake Out (immune)" in text
    assert f"{two}'s Basculegion used Aqua Jet (resisted) — {one}'s Dragonite left at 2% HP" in text
    assert " p1 " not in text and " p2 " not in text and "->" not in text
    assert "p1 roster" in outcome_summary(state)  # the BO3 and legacy rendering is unchanged


def test_wasted_actions_list_the_immune_fake_out_and_the_failed_sucker_punch(state: GameState) -> None:
    wasted = {(w.turn, w.actor_player, w.actor, w.move, w.outcome, w.target_player, w.target)
              for w in find_wasted_actions(state)}
    assert (7, "p1", "Sneasler", "Fake Out", "immune", "p2", "Froslass") in wasted
    assert (11, "p1", "Kingambit", "Sucker Punch", "failed", "", "") in wasted
    assert (3, "p1", "Basculegion", "Wave Crash", "blocked", "p2", "Froslass") in wasted


def test_action_claim_credits_a_move_to_the_side_that_used_it(state: GameState, names: tuple[str, str]) -> None:
    one, two = names
    wrong = f"On Turn 11, {two}'s Kingambit used Sucker Punch against {one}'s Dragonite."
    issues = action_claim_issues(wrong, state)
    assert len(issues) == 1 and f"it was {one}'s Kingambit that used it on turn 11" in issues[0]
    assert action_claim_issues(f"On Turn 11, {one}'s Kingambit used Sucker Punch and it failed.", state) == []
    assert narration_issues(wrong, state)  # reaches the BO1 retry


def test_action_claim_checks_turn_and_target_but_not_counterfactuals(state: GameState, names: tuple[str, str]) -> None:
    one, two = names
    assert action_claim_issues(f"On Turn 7, {one}'s Sneasler used Fake Out into {two}'s Froslass.", state) == []
    assert action_claim_issues(f"On Turn 9, {one}'s Sneasler used Fake Out.", state)  # it used it on turn 7
    assert action_claim_issues(f"On Turn 7, {two}'s Basculegion used Aqua Jet on {two}'s Sneasler.", state)  # it hit Dragonite
    assert action_claim_issues(f"On Turn 7, {two}'s Basculegion used Aqua Jet on {one}'s Dragonite.", state) == []
    assert action_claim_issues(f"On Turn 11, {two}'s Kingambit should have used Sucker Punch.", state) == []
    assert action_claim_issues(f"On Turn 11, {one}'s Kingambit used Sucker Punch instead of Iron Head.", state) == []
    # "this pivotal turn" continues the turn of the sentence before it
    text = f"The turning point was Turn 11. In this pivotal turn, {two}'s Kingambit used Sucker Punch."
    assert action_claim_issues(text, state)


def test_faint_in_a_relative_clause_is_placed_on_its_turn(state: GameState, names: tuple[str, str]) -> None:
    one, two = names
    claim = (f"On Turn 11, {two}'s Kingambit used Sucker Punch on {one}'s Dragonite, which had only 2% HP, causing a faint.")
    assert faint_timing_issues(claim, state)
    assert faint_timing_issues(f"On Turn 11, {two}'s Kingambit used Sucker Punch, causing a faint.", state) == []


def test_bo1_context_names_the_sides_and_carries_wasted_actions(sample_chaos_path, sample_replay_path) -> None:
    request = AnalysisRequest(session_id="bo1", replay_json=json.loads(sample_replay_path.read_text(encoding="utf-8")),
                              question="q")
    state = parse_replay(ShowdownReplayParser(), request)
    evidence = build_evidence(sample_chaos_path).assemble(
        request=request, game_state=state, selection=SelectionPlan(), history=[])
    context = build_explanation_context(evidence)
    assert evidence.side_names == side_names(state)
    assert "wasted_actions" in context
    players = {check["actor_player"] for check in context["turn_by_turn_checks"]}
    assert players <= set(side_names(state).values())
    assert all(check["damage_checks"] == [] or all(d["actual_damage_percent"] is None or d["actual_damage_percent"] >= 0
                                                    for d in check["damage_checks"])
               for check in context["turn_by_turn_checks"])


def test_remaining_hp_written_as_damage_is_flagged_but_damage_and_remaining_wording_are_not() -> None:
    state = ShowdownReplayParser().parse(json.loads(
        _REPLAY.with_name("gen9championsvgc2026regmb-2691414894.json").read_text(encoding="utf-8")))
    one, two = (side.player_name for side in state.sides)
    wrong = f"{one}'s Froslass used Blizzard, dealing only 47% to {two}'s Scovillain and 21% to Kingambit."
    issues = remaining_hp_as_damage_issues(wrong, state)
    assert len(issues) == 1 and "had left after a hit that dealt 53%" in issues[0]  # 21% is the real damage to Kingambit
    assert remaining_hp_as_damage_issues(f"Blizzard left {two}'s Scovillain at 47% and Kingambit at 79%.", state) == []
    assert remaining_hp_as_damage_issues("Blizzard dealt 53% to Scovillain.", state) == []


def test_percent_classifier_reads_remaining_hp_after_a_long_player_name() -> None:
    from scripts.faithfulness_benchmark.percent_classifier import classify_percent_mention

    text = "Blizzard left Player B's Scovillain at 47% and Player B's Kingambit at 79%."
    assert classify_percent_mention(text, 47.0) == "hp_remaining"
    assert classify_percent_mention(text, 79.0) == "hp_remaining"
