"""Regression: transparency for damage numbers computed under an approximation.

Reported: a deterministic verdict for Zap Cannon vs Basculegion showed
81.0%-96.4%, but a manual calc (using the Pokemon's actual in-battle Mega
Evolution, a custom forme with no entry in the calc engine's dex) gave
131.6%-156.1% — a huge, silent gap with no indication of why. Two things:

1. The parser now records when a Pokemon was observed in a different
   in-battle form (``PokemonSet.battle_formes``) without losing its move
   history (that identity-merge was fixed separately).
2. Every calc-derived verdict/turn-check now carries a `stat_caveat` that
   explains explicitly when it was computed with a Pokemon's BASE stats
   despite an observed forme change the engine's dex doesn't know — so a
   consumer (UI or the explanation LLM) never presents an approximated
   number as if it were the exact, complete result.

Also: every damage figure now carries the calc engine's own `description`
(EV/nature spread actually used), instead of only being visible in the
supplementary spotlight-matchup block.
"""

from __future__ import annotations

from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    GameState,
    MetaContext,
    PokemonSet,
    SelectionPlan,
    SideState,
)
from src.services.matchup_evaluator import MatchupEvaluator
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import FakeCalcEngine


def test_forme_caveat_empty_when_no_forme_change(fake_calc):
    attacker = PokemonSet(species="Raichu")
    defender = PokemonSet(species="Basculegion")
    assert MatchupEvaluator(fake_calc).forme_caveat(attacker, defender) == ""


class _ResolvesCalc(FakeCalcEngine):
    """Stand-in whose dex recognizes exactly one specific forme string,
    mirroring a real @smogon/calc version that has that Mega's data."""

    def __init__(self, resolvable: str) -> None:
        self._resolvable = resolvable

    def forme_resolves(self, gen: int, species: str) -> bool:
        return species == self._resolvable


def test_forme_caveat_suppressed_when_the_engine_now_resolves_the_forme():
    """Once the installed calc engine actually has data for the observed
    forme, the calc uses its real stats and the caveat must not claim base
    stats were used — a stale disclaimer on an exact number is its own kind
    of dishonesty this project's transparency rules exist to prevent."""
    ev = MatchupEvaluator(_ResolvesCalc("Staraptor-Mega"))
    attacker = PokemonSet(species="Staraptor", battle_formes=["Staraptor-Mega"])
    defender = PokemonSet(species="Raichu", battle_formes=["Raichu-Mega-Y"])
    caveat = ev.forme_caveat(attacker, defender)
    assert "Staraptor" not in caveat  # resolved -> no caveat for this mon
    assert "Raichu-Mega-Y" in caveat  # still unresolved -> caveat preserved


def test_forme_caveat_flags_attacker_and_defender_independently(fake_calc):
    ev = MatchupEvaluator(fake_calc)
    attacker = PokemonSet(species="Raichu", battle_formes=["Raichu-Mega-Y"])
    defender = PokemonSet(species="Basculegion")
    caveat = ev.forme_caveat(attacker, defender)
    assert "Raichu" in caveat and "Raichu-Mega-Y" in caveat
    assert "attacker" in caveat
    assert "defender" not in caveat  # defender had no forme change

    both = ev.forme_caveat(
        attacker, PokemonSet(species="Staraptor", battle_formes=["Staraptor-Mega"])
    )
    assert "Raichu-Mega-Y" in both and "Staraptor-Mega" in both


def test_matchup_verdict_carries_stat_caveat(fake_calc):
    attacker = PokemonSet(species="Raichu", moves=["Zap Cannon"], battle_formes=["Raichu-Mega-Y"])
    defender = PokemonSet(species="Basculegion")
    state = GameState(
        sides=[
            SideState(player="p1", team=[attacker], active=["Raichu"]),
            SideState(player="p2", team=[defender], active=["Basculegion"]),
        ]
    )
    plan = SelectionPlan(matchups=[("Raichu", "Basculegion")], focus_species=[], rationale="x")
    verdicts = MatchupEvaluator(fake_calc).evaluate(state, plan, MetaContext())
    assert len(verdicts) == 1
    assert "Raichu-Mega-Y" in verdicts[0].stat_caveat


def test_turn_check_carries_stat_caveat_and_description(fake_calc):
    attacker = PokemonSet(
        species="Raichu", moves=["Volt Switch", "Zap Cannon"], battle_formes=["Raichu-Mega-Y"]
    )
    defender = PokemonSet(species="Basculegion")
    state = GameState(
        sides=[
            SideState(player="p1", team=[attacker], active=["Raichu"]),
            SideState(player="p2", team=[defender], active=["Basculegion"]),
        ],
        outcome=BattleOutcome(
            turns=1,
            events=[
                BattleEvent(
                    turn=1, kind="move", actor="Raichu", actor_player="p1",
                    move="Volt Switch", targets=["Basculegion"],
                ),
            ],
        ),
    )
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    assert len(checks) == 1
    tc = checks[0]
    assert "Raichu-Mega-Y" in tc.stat_caveat
    assert tc.damage_checks[0].description  # EV/nature spread is visible
    assert tc.best_alternatives[0].description
