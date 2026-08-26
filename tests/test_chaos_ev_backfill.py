"""Regression: calc requests must use the Chaos-derived spread when the
replay never reveals a Pokemon's real EVs/nature, instead of silently
defaulting to 0 EVs/neutral nature.

Reported: "0 SpA Choice Specs Gholdengo Shadow Ball vs. 0 HP / 0 SpD
Annihilape" — a damage figure computed on a bare 0-EV baseline even though
the Chaos data for this exact metagame already has a clear top spread for
both Pokemon. MatchupEvaluator.enrich_set() already back-filled ability/item
from Chaos; it never touched nature/EVs at all.
"""

from __future__ import annotations

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.domain.models import MetaContext, PokemonSet, StatSpread
from src.services.matchup_evaluator import MatchupEvaluator


def test_chaos_summary_exposes_a_structured_top_spread(sample_chaos_path):
    summary = ChaosAdapter(sample_chaos_path).get_pokemon_summary("Garchomp")
    assert summary.top_spread_nature == "Jolly"
    assert summary.top_spread_evs == StatSpread(hp=16, atk=256, spe=256)


def test_unknown_species_has_no_structured_spread(sample_chaos_path):
    summary = ChaosAdapter(sample_chaos_path).get_pokemon_summary("Missingno")
    assert summary.top_spread_nature is None
    assert summary.top_spread_evs is None


def test_enrich_set_backfills_nature_and_evs_from_chaos(sample_chaos_path, fake_calc):
    meta = ChaosAdapter(sample_chaos_path).build_match_context(["Garchomp"])
    enriched = MatchupEvaluator(fake_calc).enrich_set(
        PokemonSet(species="Garchomp"), meta
    )
    assert enriched.nature == "Jolly"
    assert enriched.evs == StatSpread(hp=16, atk=256, spe=256)


def test_enrich_set_never_overrides_a_revealed_spread(sample_chaos_path, fake_calc):
    """A real replay-revealed nature/EVs must win over the Chaos guess."""
    meta = ChaosAdapter(sample_chaos_path).build_match_context(["Garchomp"])
    known = PokemonSet(species="Garchomp", nature="Adamant", evs=StatSpread(atk=252, hp=4))
    enriched = MatchupEvaluator(fake_calc).enrich_set(known, meta)
    assert enriched.nature == "Adamant"
    assert enriched.evs == StatSpread(atk=252, hp=4)


def test_enrich_set_leaves_nature_and_evs_unset_with_no_chaos_data(fake_calc):
    enriched = MatchupEvaluator(fake_calc).enrich_set(
        PokemonSet(species="Missingno"), MetaContext()
    )
    assert enriched.nature is None
    assert enriched.evs is None
