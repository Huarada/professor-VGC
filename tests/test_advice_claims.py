"""KO claims and switch advice are checked against the engine's evidence; a Protect target is no alternative (ADR-086)."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import (
    DecisionKind,
    DecisionOption,
    GameState,
    MetaContext,
    OptimalMoveOption,
    TurnCheck,
)
from src.services.advice_claims import ko_claim_issues, presence_issues, switch_advice_issues
from src.services.battle_context import outcome_summary
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import FakeCalcEngine


def _game(*turn_lines: str) -> GameState:
    lines = ["|player|p1|Ana|1|1", "|player|p2|Bob|2|1", "|poke|p1|Swampert, L50|", "|poke|p1|Venusaur, L50|",
             "|poke|p1|Grimmsnarl, L50|", "|poke|p2|Samurott-Hisui, L50|", "|poke|p2|Pelipper, L50|", "|start",
             "|switch|p1a: Swampert|Swampert, L50|100/100", "|switch|p1b: Grimmsnarl|Grimmsnarl, L50|100/100",
             "|switch|p2a: Samurott|Samurott-Hisui, L50|100/100", "|switch|p2b: Pelipper|Pelipper, L50|100/100",
             "|turn|1", *turn_lines, "|win|Bob"]
    return ShowdownReplayParser().parse({"formatid": "gen9championsvgc2026regmb", "log": "\n".join(lines)})


_STATE = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
_CHECKS = [
    TurnCheck(turn=1, actor="Swampert", actor_player="p1", move="Ice Punch", best_alternatives=[
        OptimalMoveOption(move="Wave Crash", target="Samurott-Hisui", target_player="p2", ko_chance_text="guaranteed 2HKO")]),
    TurnCheck(turn=2, actor="Swampert", actor_player="p1", move="Ice Punch", best_alternatives=[
        OptimalMoveOption(move="Wave Crash", target="Samurott-Hisui", target_player="p2", ko_chance_text="guaranteed OHKO")],
        decision_options=[DecisionOption(kind=DecisionKind.SWITCH, summary="", switch_to="Venusaur")]),
]


def test_a_guaranteed_ohko_the_engine_computed_as_a_2hko_on_that_turn_is_flagged() -> None:
    wrong = "On turn 1, using Wave Crash against Samurott-Hisui would have secured a KO due to its guaranteed OHKO."
    issues = ko_claim_issues(wrong, _STATE, _CHECKS)
    assert len(issues) == 1 and "guaranteed 2HKO" in issues[0] and "turn 1" in issues[0]
    assert ko_claim_issues(wrong.replace("turn 1", "turn 2"), _STATE, _CHECKS) == []  # it was an OHKO by then
    # the turn comes from the previous sentence of the same bullet
    two = "- On turn 1, Swampert should have attacked. Wave Crash into Samurott-Hisui was a guaranteed OHKO."
    assert ko_claim_issues(two, _STATE, _CHECKS)


def test_hedged_or_probability_ko_wording_and_unknown_pairs_are_left_alone() -> None:
    for text in ("On turn 1, Wave Crash into Samurott-Hisui risked a potential OHKO.",
                 "On turn 1, Wave Crash into Samurott-Hisui had a 31.3% chance to OHKO.",
                 "On turn 1, Wave Crash into Samurott-Hisui was a guaranteed 2HKO.",
                 "On turn 1, Ice Punch into Pelipper was a guaranteed OHKO."):  # no engine KO text for that pair
        assert ko_claim_issues(text, _STATE, _CHECKS) == [], text


def test_the_attacker_named_with_a_possessive_is_not_taken_as_the_target() -> None:
    text = "On turn 1, Samurott-Hisui's Wave Crash would be a guaranteed OHKO."
    assert ko_claim_issues(text, _STATE, _CHECKS) == []  # no target named: Samurott-Hisui is the attacker


def test_only_switches_the_engine_lists_may_be_advised() -> None:
    assert switch_advice_issues("On turn 2, switching Swampert out to Venusaur would have been safer.", _STATE, _CHECKS) == []
    assert switch_advice_issues("On turn 1, switching Swampert out to Venusaur would have been safer.", _STATE, _CHECKS)
    assert switch_advice_issues("Player A should consider switching to Grimmsnarl more often.", _STATE, _CHECKS)
    assert switch_advice_issues("On turn 1, Ana switched Swampert out for Venusaur.", _STATE, _CHECKS) == []  # narration


def test_no_alternative_or_threat_points_at_a_pokemon_behind_protect() -> None:
    state = _game("|move|p2a: Samurott|Protect|p2a: Samurott", "|-singleturn|p2a: Samurott|Protect",
                  "|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100",
                  "|move|p2b: Pelipper|Hurricane|p1a: Swampert", "|-damage|p1a: Swampert|80/100", "|turn|2",
                  "|move|p1a: Swampert|Wave Crash|p2a: Samurott", "|-damage|p2a: Samurott|50/100")
    state.sides[0].team[0].moves.append("Wave Crash")
    checks = TurnReplaySimulator(FakeCalcEngine()).simulate(state, MetaContext())
    turn1 = next(c for c in checks if c.turn == 1 and c.actor == "Swampert")
    assert all(a.target != "Samurott-Hisui" for a in turn1.best_alternatives)
    assert all(t.attacker != "Samurott-Hisui" for t in turn1.incoming_threats)
    turn2 = next(c for c in checks if c.turn == 2)
    assert any(a.target == "Samurott-Hisui" for a in turn2.best_alternatives)  # not protected on turn 2


def test_the_named_timeline_states_the_loser() -> None:
    assert "Loser: Ana. Coaching 'the losing player' means coaching Ana." in outcome_summary(_STATE, named_sides=True)


def test_the_target_is_the_pokemon_named_closest_before_the_claim() -> None:
    text = ("On turn 1, Swampert used Wave Crash against Pelipper, but it would have been better to target Samurott-Hisui, "
            "which would have taken a guaranteed OHKO.")
    issues = ko_claim_issues(text, _STATE, _CHECKS)
    assert len(issues) == 1 and "Samurott-Hisui" in issues[0]


def test_presence_advice_names_only_pokemon_that_could_follow_it() -> None:
    state = _game("|move|p2a: Samurott|Protect|p2a: Samurott", "|-singleturn|p2a: Samurott|Protect",
                  "|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
    flagged = [
        "On turn 1, Bob's Samurott-Hisui could have used Protect to stop Ice Punch.",  # it did Protect
        "On turn 1, Swampert should have attacked Samurott-Hisui instead.",  # it was behind Protect
        "On turn 1, Venusaur should have used Protect.",  # not on the field
    ]
    for text in flagged:
        assert presence_issues(text, state), text
    fine = [
        "On turn 1, Swampert could have switched to Venusaur.",  # a switch-in is off the field by definition
        "On turn 1, instead of using Protect with Samurott-Hisui, Bob could have attacked.",  # narration of what it did
        "On turn 1, consider replacing **Protect** on Samurott-Hisui with an attack.",  # its own Protect, not a target
        "Lead with Venusaur on turn 1 next time.",
    ]
    for text in fine:
        assert presence_issues(text, state) == [], text
