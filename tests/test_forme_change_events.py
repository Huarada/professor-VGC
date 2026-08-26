"""Regression: a Mega Evolution / other in-battle forme change is a real,
observable, strategically pivotal fact (e.g. Mega Gengar gaining Shadow
Tag turns a team into a trapping core) — the parser used to only record
it silently (battle_formes, for the calc-stat-approximation caveat), never
as anything the explanation AI could see in the ordered ground-truth
timeline. Now captured as its own "forme_change" BattleEvent.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import MetaContext
from src.services.battle_context import outcome_summary
from src.services.turn_simulator import TurnReplaySimulator

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|switch|p1a: Gengar|Gengar, L50, M|100/100\\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\\n"
    "|turn|1\\n"
    "|detailschange|p1a: Gengar|Gengar-Mega, L50, M\\n"
    "|-mega|p1a: Gengar|Gengar|Gengarite\\n"
    "|move|p1a: Gengar|Shadow Ball|p2a: Garchomp\\n"
    "|-damage|p2a: Garchomp|60/100\\n"
    "|win|Ash\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_forme_change_recorded_as_a_timeline_event():
    state = _state()
    forme_events = [e for e in state.outcome.events if e.kind == "forme_change"]
    assert len(forme_events) == 1
    ev = forme_events[0]
    assert ev.actor == "Gengar"
    assert ev.actor_player == "p1"
    assert ev.effects == ["Gengar-Mega"]
    assert ev.text == "p1 Gengar transformed into Gengar-Mega."


def test_forme_change_still_recorded_on_battle_formes_too():
    # The pre-existing, silent bookkeeping (used for the calc stat-caveat)
    # must be unaffected by also emitting the visible event.
    state = _state()
    gengar = next(mon for side in state.sides for mon in side.team if mon.species == "Gengar")
    assert "Gengar-Mega" in gengar.battle_formes


def test_forme_change_appears_in_the_ground_truth_timeline_text():
    summary = outcome_summary(_state())
    assert "p1 Gengar transformed into Gengar-Mega." in summary


def test_forme_change_events_do_not_confuse_the_turn_simulator():
    # kind="forme_change" must never be mistaken for a "move" event, and
    # must not crash the per-turn simulation.
    state = _state()
    checks = TurnReplaySimulator(_FakeCalc()).simulate(state, MetaContext())
    assert len(checks) == 1  # only the real "move" event (Shadow Ball)
    assert checks[0].move == "Shadow Ball"


class _FakeCalc:
    def calculate(self, request):
        from src.domain.models import DamageResult

        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, damage_rolls=[10], min_percent=10.0, max_percent=10.0,
            ko_chance_text="", is_ko_guaranteed=False, description="",
        )

    def compare_speed(self, request):
        from src.domain.models import SpeedComparison

        return SpeedComparison(
            faster=request.attacker.species, slower=request.defender.species,
            faster_speed=100, slower_speed=50,
        )

    def forme_resolves(self, gen, species):
        return True
