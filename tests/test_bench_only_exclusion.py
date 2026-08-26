"""Regression: team-preview-only Pokemon (never switched in) must never be
treated as "in play" — not as a deterministic-calc matchup, not in the
cross-side fallback, and not in suggestion context.

This reproduces the exact class of bug reported against a real replay: the
6-mon team preview listed "Whimsicott" for a side, but only 4 of those 6 were
ever brought into the actual battle (Whimsicott was benched). The pipeline
mistakenly evaluated a "Raichu vs Whimsicott" matchup (real numbers, wrong
Pokemon) and the explanation LLM then narrated it as if it had happened.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.services.battle_context import candidate_species, rosters
from src.services.selection_logic import cross_side_matchups, fallback_plan

_LOG = (
    "|player|p1|a|1|1058\n|player|p2|b|2|1065\n|clearpoke\n"
    "|poke|p1|Raichu, L50, M|\n"
    "|poke|p2|Whimsicott, L50, F|\n"
    "|poke|p2|Garchomp, L50, F|\n"
    "|teampreview|4\n|start\n"
    "|switch|p1a: Raichu|Raichu, L50, M|100/100\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\n"
    "|turn|1\n"
    "|move|p1a: Raichu|Zap Cannon|p2a: Garchomp\n"
    "|move|p2a: Garchomp|Earthquake|p1a: Raichu\n"
    "|win|a\n"
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_side_of_excludes_bench_only_pokemon():
    state = _state()
    side_of = state.side_of()
    assert side_of.get("Garchomp") == "p2"
    assert "Whimsicott" not in side_of


def test_involved_species_still_lists_the_full_preview_for_reference():
    # involved_species() intentionally keeps the full team-preview roster; it
    # is only used as a last-resort fallback, never for matchups/suggestions.
    assert "Whimsicott" in _state().involved_species()


def test_candidate_species_and_rosters_exclude_bench_only_pokemon():
    state = _state()
    assert "Whimsicott" not in candidate_species(state)
    assert "Whimsicott" not in rosters(state)["p2"]


def test_cross_side_fallback_never_pairs_bench_only_pokemon():
    state = _state()
    side_of = state.side_of()
    pairs = cross_side_matchups(side_of, limit=10)
    assert pairs  # still produces real cross-side pairs
    assert all("Whimsicott" not in pair for pair in pairs)


def test_fallback_plan_never_includes_bench_only_pokemon():
    state = _state()
    species = candidate_species(state)
    plan = fallback_plan(species, state.side_of())
    flat = {name for pair in plan.matchups for name in pair}
    assert "Whimsicott" not in flat
    assert flat <= set(species)
