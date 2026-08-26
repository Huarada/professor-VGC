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
    # Both _damage_checks and _best_alternatives issue their own
    # CalcRequest per move event (best_alternatives re-checks Earthquake
    # too, since it's this Garchomp's only confirmed move) — every one of
    # them, across both turns, must see the still-active -1 def with no
    # intervening switch.
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert len(damage_calls) == 4
    assert all(r.defender.boosts == {"def": -1} for r in damage_calls)


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
    damage_calls = [r for r in calc.requests if r.move == "Earthquake"]
    assert len(damage_calls) == 4  # 2 calc calls (damage_checks + best_alternatives) per turn
    before_switch, after_switch = damage_calls[:2], damage_calls[2:]
    assert all(r.defender.boosts == {"def": -1} for r in before_switch)
    assert all(r.defender.boosts == {} for r in after_switch), (
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
