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
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    PokemonSet,
    SideState,
    SpeedComparison,
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

    # Thunder Wave is a genuine 0-power status move (paralysis only) and is
    # never a candidate for "optimal damage". Fake Out IS a damaging move
    # (40 BP) despite its utility reputation, and MUST be run through the
    # calc like any other attack — excluding it here used to mean the engine
    # never caught a suggestion to use it against a Ghost-type (immune to
    # Normal), since no real damage figure was ever computed for it.
    assert "Thunder Wave" not in moves
    assert moves == {"Tackle", "Fake Out", "Earthquake", "Hyper Beam"}

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


class _TypeAwareCalcEngine:
    """Stand-in that actually respects Normal-vs-Ghost immunity, unlike
    ``FakeCalcEngine`` (which always returns a positive figure regardless of
    move/species) — needed to prove best_alternatives surfaces a REAL 0%
    for an immune move instead of silently omitting it."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        immune = request.move == "Fake Out" and request.defender.species == "Gholdengo"
        pct = 0.0 if immune else 50.0
        return DamageResult(
            attacker=request.attacker.species,
            defender=request.defender.species,
            move=request.move,
            damage_rolls=[0, 0] if immune else [50, 55],
            min_percent=pct,
            max_percent=pct,
            ko_chance_text="" if immune else "guaranteed 2HKO",
            is_ko_guaranteed=False,
            description=(
                f"{request.attacker.species} {request.move} vs {request.defender.species}"
                + (" -- no effect (immune)" if immune else "")
            ),
        )

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        return SpeedComparison(
            faster=request.attacker.species, slower=request.defender.species,
            faster_speed=120, slower_speed=60,
        )

    def forme_resolves(self, gen: int, species: str) -> bool:
        return False

    def close(self) -> None:  # pragma: no cover
        pass


def test_best_alternatives_surfaces_real_immunity_instead_of_omitting_the_move():
    """A Ghost-type target is immune to Fake Out (Normal-type) — the engine
    must report that as a real, calc-verified 0%, not silently drop Fake
    Out from best_alternatives (which would leave the explanation model
    with no grounded data to catch a bad "use Fake Out here" suggestion)."""
    attacker = PokemonSet(species="Incineroar", moves=["Fake Out", "Flare Blitz"])
    defender = PokemonSet(species="Gholdengo")
    state = GameState(
        sides=[
            SideState(player="p1", team=[attacker], active=["Incineroar"]),
            SideState(player="p2", team=[defender], active=["Gholdengo"]),
        ],
        outcome=BattleOutcome(
            turns=1,
            events=[
                BattleEvent(
                    turn=1, kind="move", actor="Incineroar", actor_player="p1",
                    move="Flare Blitz", targets=["Gholdengo"],
                ),
            ],
        ),
    )
    checks = TurnReplaySimulator(_TypeAwareCalcEngine()).simulate(state, MetaContext())
    alts = {a.move: a for a in checks[0].best_alternatives}

    assert "Fake Out" in alts, "Fake Out must still be a candidate, not silently dropped"
    assert alts["Fake Out"].max_percent == 0.0
    assert "immune" in alts["Fake Out"].description.lower()


def test_best_alternatives_never_invents_a_move_not_in_the_confirmed_set(fake_calc):
    """Only the moves this Pokemon is confirmed to know this game are candidates."""
    state = _state(["Tackle"])
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    alts = checks[0].best_alternatives
    assert [a.move for a in alts] == ["Tackle"]
