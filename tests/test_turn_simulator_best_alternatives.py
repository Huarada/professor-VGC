"""Tests for the per-turn optimal-play feedback loop (TurnCheck.best_alternatives).

For every real turn, the engine is re-consulted again for every OTHER move
confirmed for that Pokemon this game (never a guessed move), against that
turn's real target/field, ranked best-first (OHKO beats everything else, then
highest damage). This validates the optimal play deterministically instead of
only checking the move that was actually used.
"""

from __future__ import annotations

from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    GameState,
    MetaContext,
    PokemonSet,
    SideState,
)
from src.services.turn_simulator import TurnReplaySimulator


def _state(attacker_moves: list[str]) -> GameState:
    attacker = PokemonSet(species="Garchomp", moves=attacker_moves)
    defender = PokemonSet(species="Talonflame")
    return GameState(
        sides=[
            SideState(player="p1", team=[attacker], active=["Garchomp"]),
            SideState(player="p2", team=[defender], active=["Talonflame"]),
        ],
        outcome=BattleOutcome(
            turns=1,
            events=[
                BattleEvent(
                    turn=1, kind="move", actor="Garchomp", actor_player="p1",
                    move="Tackle", targets=["Talonflame"],
                ),
            ],
        ),
    )


def test_best_alternatives_excludes_status_moves_and_is_ranked(fake_calc):
    state = _state(["Tackle", "Fake Out", "Earthquake", "Hyper Beam", "Thunder Wave"])
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    assert len(checks) == 1
    alts = checks[0].best_alternatives
    moves = {a.move for a in alts}

    # Status moves (Fake Out, Thunder Wave) are never candidates for "optimal damage".
    assert "Fake Out" not in moves
    assert "Thunder Wave" not in moves
    assert moves == {"Tackle", "Earthquake", "Hyper Beam"}

    # Ranked best-first by projected damage.
    percents = [a.max_percent for a in alts]
    assert percents == sorted(percents, reverse=True)
    for alt in alts:
        assert alt.target == "Talonflame"


def test_best_alternatives_caps_at_four(fake_calc):
    moves = ["Tackle", "Earthquake", "Hyper Beam", "Rock Slide", "Ice Beam", "Flamethrower"]
    state = _state(moves)
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    assert len(checks[0].best_alternatives) == 4


def test_best_alternatives_never_invents_a_move_not_in_the_confirmed_set(fake_calc):
    """Only the moves this Pokemon is confirmed to know this game are candidates."""
    state = _state(["Tackle"])
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    alts = checks[0].best_alternatives
    assert [a.move for a in alts] == ["Tackle"]
