"""Tests for the Chaos metagame adapter."""

from __future__ import annotations

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


def test_species_name_normalization(sample_chaos_path):
    from src.adapters.chaos.chaos_adapter import ChaosAdapter

    adapter = ChaosAdapter(sample_chaos_path)
    # Exact forme key present in the sample.
    assert adapter.get_pokemon_summary("Typhlosion-Hisui").top_moves
    # A mega spelling resolves to the base entry via the fallback.
    assert adapter.get_pokemon_summary("Metagross-Mega").top_moves
