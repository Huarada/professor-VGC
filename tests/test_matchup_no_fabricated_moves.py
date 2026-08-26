"""Regression: the deterministic matchup evaluator must never invent a move.

Reported bug: a verdict was produced for "Garchomp -> Talonflame using Tackle"
even though Garchomp never had Tackle in its actual moveset for that game.
Two independent causes were found and fixed:

1. The parser fragmented a Pokemon's identity across a Mega Evolution: the
   post-Mega switch-in was registered as a brand-new, moveless entry (e.g.
   "Staraptor-Mega") instead of being merged into the existing "Staraptor"
   entry that already held its real moves. If that fresh, moveless fragment
   got picked as the calc attacker, it had nothing real to attack with.
2. Even for a genuinely moveless Pokemon (never observed attacking), the
   evaluator silently substituted a hardcoded "Tackle" placeholder instead of
   skipping the matchup.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import GameState, MetaContext, PokemonSet, SelectionPlan
from src.services.matchup_evaluator import MatchupEvaluator

_MEGA_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|poke|p1|Talonflame, L50|\\n|poke|p2|Staraptor, L50|\\n|start\\n"
    "|switch|p1a: Talonflame|Talonflame, L50|100/100\\n"
    "|switch|p2a: Staraptor|Staraptor, L50|100/100\\n|turn|1\\n"
    "|detailschange|p2a: Staraptor|Staraptor-Mega, L50\\n"
    "|-mega|p2a: Staraptor|Staraptor|Staraptite\\n"
    "|move|p2a: Staraptor|Brave Bird|p1a: Talonflame\\n"
    "|-damage|p1a: Talonflame|40/100\\n|turn|2\\n"
    "|switch|p2a: Staraptor|Staraptor-Mega, L50|68/100\\n"
    "|move|p1a: Talonflame|Flare Blitz|p2a: Staraptor\\n"
    "|-damage|p2a: Staraptor|10/100\\n|win|Ash\\n"
    '"}'
)


def test_mega_evolution_reswitch_does_not_fragment_roster_or_moves():
    state = ShowdownReplayParser().parse(_MEGA_LOG)
    p2 = next(s for s in state.sides if s.player == "p2")
    species_keys = [mon.species for mon in p2.team]
    assert species_keys == ["Staraptor"]  # no "Staraptor-Mega" ghost entry
    staraptor = next(mon for mon in p2.team if mon.species == "Staraptor")
    assert "Brave Bird" in staraptor.moves
    assert p2.active == ["Staraptor"]
    # The Mega Evolution itself is still recorded (for the stat-caveat check),
    # just without fragmenting the roster/move history.
    assert staraptor.battle_formes == ["Staraptor-Mega"]


def test_evaluator_skips_matchup_for_moveless_pokemon(fake_calc):
    """A Pokemon with zero observed moves gets no verdict, not a fake move."""
    state = GameState(sides=[])
    evaluator = MatchupEvaluator(fake_calc)
    selection = SelectionPlan(
        focus_species=["Garchomp", "Talonflame"],
        matchups=[("Garchomp", "Talonflame")],
        rationale="x",
    )
    # Neither Pokemon exists in game_state.sides, so index_sets() has nothing
    # for them and enrich_set() falls back to a fresh, moveless PokemonSet —
    # exactly the "never actually observed attacking" case.
    verdicts = evaluator.evaluate(state, selection, MetaContext())
    assert verdicts == []


def test_evaluator_uses_only_observed_moves(fake_calc):
    garchomp = PokemonSet(species="Garchomp", moves=["Earthquake"])
    talonflame = PokemonSet(species="Talonflame", moves=[])
    state = GameState(
        sides=[
            {"player": "p1", "team": [garchomp.model_dump()], "active": ["Garchomp"]},
            {"player": "p2", "team": [talonflame.model_dump()], "active": ["Talonflame"]},
        ]
    )
    evaluator = MatchupEvaluator(fake_calc)
    selection = SelectionPlan(
        focus_species=["Garchomp", "Talonflame"],
        matchups=[("Garchomp", "Talonflame"), ("Talonflame", "Garchomp")],
        rationale="x",
    )
    verdicts = evaluator.evaluate(state, selection, MetaContext())
    # Garchomp (has a real move) -> Talonflame produces a verdict using that move.
    assert len(verdicts) == 1
    assert verdicts[0].attacker == "Garchomp"
    assert verdicts[0].best_move == "Earthquake"
    # Talonflame (no observed moves) -> Garchomp is skipped, never "Tackle".
    assert all(v.attacker != "Talonflame" for v in verdicts)
