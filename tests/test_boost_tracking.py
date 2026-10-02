"""Tests for the stat-boost ledger threaded into turn-by-turn calc requests.

Reported: a damage/speed projection never reflected a REAL, observed stat
stage change (Intimidate, a setup move, ...) that happened earlier in the
same game — every calc silently assumed +0 on every stat, always. The
ledger built in TurnReplaySimulator.simulate() now applies the actual,
point-in-time stage to both the attacker and the defender of every calc,
and resets to neutral the moment that Pokemon switches back in (stages
don't persist across a switch in the real game either).

Uses a local recording fake (not the shared `fake_calc` fixture, which
ignores its inputs) so each CalcRequest's own attacker/defender.boosts can
be asserted on directly.
"""

from __future__ import annotations

from tests.conftest import fake_move_info
from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    PokemonSet,
    SideState,
    SpeedComparison,
)
from src.services.turn_simulator import TurnReplaySimulator


class _RecordingCalc:
    """Records every CalcRequest it receives; returns a fixed, harmless
    result (boost VALUES actually changing real damage/speed numbers is
    already live-verified against the real @smogon/calc engine — this
    fake only needs to prove the RIGHT boosts dict reached the request)."""

    def __init__(self) -> None:
        self.requests: list[CalcRequest] = []

    def calculate(self, request: CalcRequest) -> DamageResult:
        self.requests.append(request)
        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, damage_rolls=[10, 12], min_percent=10.0, max_percent=12.0,
            ko_chance_text="3HKO", is_ko_guaranteed=False, description="",
        )

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        self.requests.append(request)
        return SpeedComparison(
            faster=request.attacker.species, slower=request.defender.species,
            faster_speed=100, slower_speed=50,
        )

    def move_info(self, gen, move):  # noqa: ANN001, ANN201 - test double
        return fake_move_info(move)

    def forme_resolves(self, gen: int, species: str) -> bool:
        return True


def _event(turn, kind, actor, actor_player, **kwargs) -> BattleEvent:
    return BattleEvent(turn=turn, kind=kind, actor=actor, actor_player=actor_player, **kwargs)


def test_attacker_boost_applied_to_its_own_calc_request():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Garchomp", "p1", effects=["atk", "2"]),
        _event(
            1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"],
        ),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert damage_calls, "no Earthquake CalcRequest was made"
    assert damage_calls[0].attacker.boosts == {"atk": 2}


def test_defender_boost_applied_too_not_just_the_attacker():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Whimsicott", "p2", effects=["def", "-1"]),
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert damage_calls[0].defender.boosts == {"def": -1}


def test_boost_persists_across_turns_until_switch():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Whimsicott", "p2", effects=["def", "-1"]),
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
        _event(2, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=2, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    # Identical CalcRequests are memoized within one simulation, so turn 2
    # (same field, same stages) reuses turn 1's engine answer — which is only
    # correct because the still-active -1 def is part of the request: every
    # request issued, on either turn, must carry it.
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert damage_calls
    assert all(r.defender.boosts == {"def": -1} for r in damage_calls)
    checks = TurnReplaySimulator(calc).simulate(state, MetaContext())
    assert [c.turn for c in checks] == [1, 2]
    assert checks[0].damage_checks == checks[1].damage_checks


def test_boost_resets_on_switch_out_and_back_in():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Whimsicott", "p2", effects=["def", "-1"]),
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
        # Whimsicott switches out (a different p2 mon takes the field)...
        _event(2, "switch", "Incineroar", "p2"),
        # ...then switches back in: its stages must be back to neutral.
        _event(3, "switch", "Whimsicott", "p2"),
        _event(3, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(
                player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])],
                active=["Garchomp"],
            ),
            SideState(
                player="p2",
                team=[PokemonSet(species="Whimsicott"), PokemonSet(species="Incineroar")],
                active=["Whimsicott", "Incineroar"],
            ),
        ],
        outcome=BattleOutcome(turns=3, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    # Identical requests are memoized, so each distinct (boost state) shows
    # up once: first with the -1 def, then — after the switch — neutral.
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert [r.defender.boosts for r in damage_calls] == [{"def": -1}, {}], (
        "boost must reset after switching back in"
    )


def test_boost_clamped_to_the_real_minus6_plus6_range():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Garchomp", "p1", effects=["atk", "6"]),
        _event(1, "boost", "Garchomp", "p1", effects=["atk", "6"]),  # would be +12 uncapped
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert damage_calls[0].attacker.boosts == {"atk": 6}


def test_speed_comparison_also_reflects_the_defenders_current_boost():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "boost", "Whimsicott", "p2", effects=["spe", "1"]),
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    speed_calls = [r for r in calc.requests if r.move == "Tackle"]
    assert speed_calls, "no speed-comparison CalcRequest was made"
    assert speed_calls[0].defender.boosts == {"spe": 1}


def test_no_boost_events_leaves_boosts_empty_as_before():
    events = [
        _event(1, "switch", "Garchomp", "p1"),
        _event(1, "switch", "Whimsicott", "p2"),
        _event(1, "move", "Garchomp", "p1", move="Earthquake", targets=["Whimsicott"]),
    ]
    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp", moves=["Earthquake"])], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Whimsicott")], active=["Whimsicott"]),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert damage_calls[0].attacker.boosts == {}
    assert damage_calls[0].defender.boosts == {}
