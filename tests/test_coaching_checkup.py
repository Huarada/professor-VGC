"""The six-point checkup can fail on each rule it checks (a pass is not a vacuous one)."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.coaching_checkup import answer_failures, evidence_failures, protected_by_turn, real_player_names
from src.domain.models import DecisionKind, DecisionOption, OptimalMoveOption, ProtectRead, ThreatCheck, TurnCheck, TurnDamageCheck
from tests.test_advice_claims import _game

_LOG = ("|switch|p2a: Sam|Samurott-Hisui, L50|100/100\n|turn|1\n|move|p2a: Sam|Protect|p2a: Sam\n"
        "|-singleturn|p2a: Sam|Protect\n|turn|2\n|-singleturn|p2a: Sam|move: Detect")


def test_protect_is_read_from_the_raw_log_by_species() -> None:
    assert protected_by_turn(_LOG) == {1: {("p2", "Samurott-Hisui")}, 2: {("p2", "Samurott-Hisui")}}


def test_each_evidence_rule_can_fail() -> None:
    state = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
    damage = TurnDamageCheck(attacker="Swampert", attacker_player="p1", target="Grimmsnarl", target_player="p1",
                             move="Earthquake", projected_min_percent=0, projected_max_percent=0)
    check = TurnCheck(turn=1, actor="Swampert", actor_player="p1", move="Ice Punch",
                      best_alternatives=[OptimalMoveOption(move="Wave Crash", target="Samurott-Hisui", target_player="p2")],
                      incoming_threats=[ThreatCheck(attacker="Samurott-Hisui", attacker_player="p2", move="Ceaseless Edge")],
                      decision_options=[DecisionOption(kind=DecisionKind.SWITCH, summary="", switch_to="Venusaur",
                                                       max_percent_taken=62.0)])
    evidence = SimpleNamespace(turn_checks=[check], battle_result="Winner: Bob.", protect_reads=[ProtectRead(
        turn=1, blocker="Grimmsnarl", blocker_player="p1", attacker="Swampert", attacker_player="p1", move="Earthquake",
        is_spread_move=True, value_denied=damage, is_genuine_read=True, was_immediate_ko_threat=False,
        misallocated=False)])
    game = {"players": ["Ana", "Bob"], "log": _LOG}
    fails = evidence_failures(game, state, evidence, ["Ana", "Bob"])
    assert fails["1_allies"] and fails["2_switches"] and len(fails["3_alternatives"]) == 2
    assert fails["5_coaching"] and fails["6_names"] == ["a real player name reaches the model"]


def test_each_answer_rule_can_fail() -> None:
    state = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
    answer = "### Turning point\nOn turn 1, Venusaur should have used Protect. Bob should have attacked Swampert. Ana lost."
    fails = answer_failures(answer, "coach_loser", "Coach the losing player.", state, [], ["Ana"])
    assert fails["3_alternatives"] and fails["5_coaching"] and fails["6_names"] and fails["7_turning"]
    clean = answer_failures("Ana's Swampert should have used Protect on turn 1.", "coach_loser", "Coach the losing player.",
                            state, [], [])
    assert not any(clean.values()), clean


def test_real_names_come_from_the_players_and_the_log() -> None:
    assert real_player_names({"players": ["Ana"], "log": "|player|p2|Robert|1|\n|player|p1|Ana|2|"}) == ["Ana", "Robert"]


def test_a_partner_is_flagged_only_when_the_waste_is_said_of_it(monkeypatch) -> None:
    import src.services.wasted_actions as wasted
    from src.domain.models import AllySynergy

    synergy = AllySynergy(turn=1, actor="Swampert", actor_player="p1", move="Earthquake", ally="Grimmsnarl", result="")
    monkeypatch.setattr(wasted, "find_ally_synergies", lambda state: [synergy])
    state = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")

    def allies(text: str) -> list[str]:
        return answer_failures(text, "decisions", "Which decisions?", state, [], [])["1_allies"]

    assert allies("On turn 1, Earthquake was wasted on Grimmsnarl.")
    assert not allies("On turn 1, Earthquake had no effect on Pelipper (immune) while Grimmsnarl took nothing.")
    assert not allies("On turn 1, Earthquake was wasted on Grimmsnarl, its partner.")


def test_a_passive_sentence_about_the_winner_is_not_coaching_the_winner() -> None:
    state = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
    coaching = answer_failures("Bob's Pelipper could have been KOed on turn 1.", "coach_loser", "Coach the loser.", state, [], [])
    assert not coaching["5_coaching"]


def test_in_a_mirror_only_the_named_partner_counts(monkeypatch) -> None:
    import src.services.wasted_actions as wasted
    from src.domain.models import AllySynergy

    state = _game("|move|p1a: Swampert|Ice Punch|p2b: Pelipper", "|-damage|p2b: Pelipper|70/100", "|turn|2")
    synergy = AllySynergy(turn=1, actor="Samurott-Hisui", actor_player="p2", move="Earthquake", ally="Pelipper", result="")
    monkeypatch.setattr(wasted, "find_ally_synergies", lambda _: [synergy])
    state.sides[0].team.append(state.sides[1].team[1].model_copy())  # Ana brought a Pelipper too

    def allies(text: str) -> list[str]:
        return answer_failures(text, "decisions", "Which decisions?", state, [], [])["1_allies"]

    assert not allies("On turn 1, Earthquake had no effect on Pelipper, which was immune.")  # could be Ana's
    assert allies("On turn 1, Earthquake was wasted on Bob's Pelipper.")
