"""Tests for the Chaos metagame adapter."""

from __future__ import annotations

import json

import pytest

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.domain.exceptions import ChaosDataError


def test_loads_metagame(sample_chaos_path):
    assert ChaosAdapter(sample_chaos_path).metagame == "gen9championsvgc2026regmb"


def test_summary_extracts_top_n(sample_chaos_path):
    summary = ChaosAdapter(sample_chaos_path, top_n=3).get_pokemon_summary("Garchomp")
    assert "Rough Skin" in summary.top_abilities
    assert summary.top_abilities["Rough Skin"] == pytest.approx(0.945, abs=1e-3)
    assert "Life Orb" in summary.top_items
    assert "Dragon Claw" in summary.top_moves


def test_spread_ev_divider_conversion(sample_chaos_path):
    summary = ChaosAdapter(sample_chaos_path).get_pokemon_summary("Garchomp")
    assert any("256 Spe" in s and "256 Atk" in s for s in summary.top_spreads)


def test_checks_and_counters_sorted_by_p(sample_chaos_path):
    summary = ChaosAdapter(sample_chaos_path).get_pokemon_summary("Sinistcha")
    assert list(summary.threats_winrate.values())[0] == pytest.approx(0.64)


def test_unknown_species_returns_empty(sample_chaos_path):
    assert ChaosAdapter(sample_chaos_path).get_pokemon_summary("Missingno").top_moves == {}


def test_build_match_context(sample_chaos_path):
    ctx = ChaosAdapter(sample_chaos_path).build_match_context(["Sinistcha", "Garchomp"])
    assert ctx.metagame == "gen9championsvgc2026regmb"
    assert set(ctx.pokemon_stats) == {"Sinistcha", "Garchomp"}


def test_missing_file_raises():
    with pytest.raises(ChaosDataError):
        ChaosAdapter("/nonexistent/chaos.json")


def test_percentages_use_the_category_own_weight_not_raw_count(tmp_path):
    """Reported live, against a real Smogon dump: "Raw count" and a
    category's own weighted total (Abilities/Items/... all summing to the
    SAME value, confirmed directly against real data) are two DIFFERENT
    numbers — dividing by "Raw count" gave Incineroar's Intimidate an
    absurd ~0.3% instead of its real ~99.6%. This project's own bundled
    sample_data/*.json happens to set Raw count EQUAL to sum(Abilities) (a
    hand-authored fixture, not a real scrape), which is the one reason the
    existing test_summary_extracts_top_n above never caught this — this
    fixture deliberately makes them differ, like real Smogon data does."""
    payload = {
        "info": {"metagame": "gen9vgc2025regh"},
        "data": {
            "Incineroar": {
                "Raw count": 1_000_000,  # deliberately unrelated to the sums below
                "Abilities": {"Intimidate": 996.0, "Blaze": 4.0},  # sums to 1000
                "Items": {"Sitrus Berry": 600.0, "Safety Goggles": 400.0},  # sums to 1000
                "Moves": {"Fake Out": 3000.0, "Flare Blitz": 1000.0},  # sums to 4000 (own total)
            }
        },
    }
    path = tmp_path / "gen9vgc2025regh-0.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = ChaosAdapter(path).get_pokemon_summary("Incineroar")
    assert summary.top_abilities["Intimidate"] == pytest.approx(0.996)
    assert summary.top_items["Sitrus Berry"] == pytest.approx(0.6)
    assert summary.top_moves["Fake Out"] == pytest.approx(0.75)


def test_species_name_normalization(sample_chaos_path):
    from src.adapters.chaos.chaos_adapter import ChaosAdapter

    adapter = ChaosAdapter(sample_chaos_path)
    # Exact forme key present in the sample.
    assert adapter.get_pokemon_summary("Typhlosion-Hisui").top_moves
    # A mega spelling resolves to the base entry via the fallback.
    assert adapter.get_pokemon_summary("Metagross-Mega").top_moves
