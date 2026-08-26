"""Regression: `-boost`/`-unboost` log lines are real, observable ground
truth (Intimidate, a setup move, a self-drop from Superpower/Leaf Storm,
...) — the parser used to drop these entirely, so no calc downstream ever
reflected an actually-active stat stage. Confirmed activations are now
captured as their own ordered "boost" BattleEvents, consumed by
TurnReplaySimulator to build a per-Pokemon stage ledger (see
test_boost_tracking.py for that half).
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|switch|p1a: Incineroar|Incineroar, L50, M|100/100\\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\\n"
    "|-ability|p1a: Incineroar|Intimidate|boost\\n"
    "|-unboost|p2a: Garchomp|atk|1\\n"
    "|turn|1\\n"
    "|move|p2a: Garchomp|Swords Dance|p2a: Garchomp\\n"
    "|-boost|p2a: Garchomp|atk|2\\n"
    "|move|p1a: Incineroar|Fake Out|p2a: Garchomp\\n"
    "|-damage|p2a: Garchomp|90/100\\n"
    "|-unboost|p1a: Incineroar|accuracy|1\\n"
    "|win|Ash\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_unboost_recorded_as_a_negative_delta_event():
    state = _state()
    boost_events = [e for e in state.outcome.events if e.kind == "boost"]
    intimidate_drop = next(e for e in boost_events if e.turn == 0)
    assert intimidate_drop.actor == "Garchomp"
    assert intimidate_drop.actor_player == "p2"
    assert intimidate_drop.effects == ["atk", "-1"]
    assert "fell by 1 stage" in intimidate_drop.text


def test_boost_recorded_as_a_positive_delta_event():
    state = _state()
    boost_events = [e for e in state.outcome.events if e.kind == "boost" and e.turn == 1]
    swords_dance = next(e for e in boost_events if e.actor == "Garchomp")
    assert swords_dance.actor_player == "p2"
    assert swords_dance.effects == ["atk", "2"]
    assert "rose by 2 stages" in swords_dance.text


def test_accuracy_and_evasion_changes_are_not_tracked():
    # Neither the damage calc nor this project's field model uses
    # accuracy/evasion stages — only the five stats that actually feed
    # @smogon/calc are captured.
    state = _state()
    boost_events = [e for e in state.outcome.events if e.kind == "boost"]
    assert all(e.effects[0] != "accuracy" for e in boost_events)
    assert len(boost_events) == 2  # Intimidate's -1 atk + Swords Dance's +2 atk only


def test_boost_events_are_ordered_alongside_moves_in_the_same_timeline():
    state = _state()
    kinds_in_order = [e.kind for e in state.outcome.events]
    # Swords Dance's own "boost" event must come immediately after its
    # "move" event, matching the real log order.
    move_idx = kinds_in_order.index("move")
    assert kinds_in_order[move_idx] == "move"
    assert kinds_in_order[move_idx + 1] == "boost"
