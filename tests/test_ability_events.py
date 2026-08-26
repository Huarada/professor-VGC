"""Regression: ability activations (Intimidate, Trace, Download, ...) are
real, observable ground truth Showdown emits via `-ability` — the parser
used to drop these entirely, leaving the explanation AI with zero
turn-by-turn visibility into abilities (reported: "senti falta da
descricao de habilidade turno a turno"). Confirmed activations are now
captured as their own ordered timeline event AND back-filled onto the
PokemonSet itself, so later calc requests use the real ability instead of
a Chaos-guessed one.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|switch|p1a: Incineroar|Incineroar, L50, M|100/100\\n"
    "|-ability|p1a: Incineroar|Intimidate|boost\\n"
    "|-unboost|p2a: Garchomp|atk|1\\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\\n"
    "|turn|1\\n"
    "|move|p1a: Incineroar|Flare Blitz|p2a: Garchomp\\n"
    "|-damage|p2a: Garchomp|20/100\\n"
    "|win|Ash\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_ability_activation_recorded_as_timeline_event():
    state = _state()
    ability_events = [e for e in state.outcome.events if e.kind == "ability"]
    assert len(ability_events) == 1
    ev = ability_events[0]
    assert ev.actor == "Incineroar"
    assert ev.actor_player == "p1"
    assert ev.effects == ["Intimidate"]
    assert "Intimidate" in ev.text and "activated" in ev.text


def test_ability_events_do_not_confuse_the_turn_simulator_move_loop():
    # kind="ability" events must never be mistaken for a "move" event
    # downstream — the turn simulator only ever processes kind == "move".
    state = _state()
    kinds = {e.kind for e in state.outcome.events}
    assert "ability" in kinds and "move" in kinds
    move_events = [e for e in state.outcome.events if e.kind == "move"]
    assert all(e.move for e in move_events)


def test_confirmed_ability_backfilled_onto_the_pokemon_set():
    state = _state()
    incineroar = next(
        mon
        for side in state.sides
        for mon in side.team
        if mon.species == "Incineroar"
    )
    assert incineroar.ability == "Intimidate"


def test_second_ability_reveal_does_not_overwrite_the_first():
    # A later `-ability` line for the SAME mon (e.g. a re-trigger on a
    # second switch-in) must not clobber the already-confirmed ability.
    log = (
        '{"formatid":"gen9championsvgc2026regmb","log":"'
        "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
        "|switch|p1a: Incineroar|Incineroar, L50, M|100/100\\n"
        "|-ability|p1a: Incineroar|Intimidate|boost\\n"
        "|switch|p1a: Incineroar|Incineroar, L50, M|100/100\\n"
        "|-ability|p1a: Incineroar|Intimidate|boost\\n"
        "|win|Ash\\n"
        '"}'
    )
    state = ShowdownReplayParser().parse(log)
    incineroar = next(mon for side in state.sides for mon in side.team if mon.species == "Incineroar")
    assert incineroar.ability == "Intimidate"
    assert len([e for e in state.outcome.events if e.kind == "ability"]) == 2
