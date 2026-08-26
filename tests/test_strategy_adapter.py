"""Tests for the Smogon (Chaos-derived) strategy adapter."""

from __future__ import annotations

from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.models import Archetype


def test_infers_trick_room_archetype(sample_chaos_path):
    strat = ChaosStrategyAdapter(sample_chaos_path).get_strategy("Sinistcha")
    assert Archetype.TRICK_ROOM in strat.archetypes
    assert "Garchomp" in strat.common_teammates
    assert strat.common_sets


def test_unknown_species_graceful(sample_chaos_path):
    strat = ChaosStrategyAdapter(sample_chaos_path).get_strategy("Missingno")
    assert strat.species == "Missingno" and strat.overview
