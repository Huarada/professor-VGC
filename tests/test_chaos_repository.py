"""Tests for rating-tier selection and regulation-fallback in the Chaos layer."""

from __future__ import annotations

import json

import pytest

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.adapters.chaos.chaos_repository import ChaosRepository


def _write(dirpath, metagame, cutoff, data):
    payload = {"info": {"metagame": metagame}, "data": data}
    (dirpath / f"{metagame}-{cutoff}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.fixture
def chaos_dir(tmp_path):
    mon = lambda: {  # noqa: E731
        "Raw count": 100, "Abilities": {"Levitate": 100}, "Items": {"Leftovers": 50},
        "Moves": {"Protect": 90, "Earth Power": 80}, "Spreads": {"Modest:0/0/0/32/0/32": 40},
        "Teammates": {"Incineroar": 30}, "Checks and Counters": {"Ting-Lu": {"p": 0.6}},
    }
    d = tmp_path / "chaos"
    d.mkdir()
    _write(d, "gen9championsvgc2026regmb", 1760, {"Chi-Yu": mon(), "Flutter Mane": mon()})
    _write(d, "gen9championsvgc2026regmb", 1500, {"Chi-Yu": mon()})
    _write(d, "gen9championsvgc2026regmb", 0, {"Chi-Yu": mon()})
    # previous regulation, same game: only has Ogerpon
    _write(d, "gen9championsvgc2026regma", 1760, {"Ogerpon": mon()})
    # a DIFFERENT game (base VGC) — must never be used as fallback for Champions
    _write(d, "gen9vgc2026regmb", 1760, {"Miraidon": mon()})
    return d


def test_ideal_and_current_tier(chaos_dir):
    repo = ChaosRepository(chaos_dir)
    meta = "gen9championsvgc2026regmb"
    assert repo.ideal_file(meta).cutoff == 1760
    assert repo.current_file(meta, 1206).cutoff == 0  # 0 <= 1206 < 1500
    assert repo.current_file(meta, 1550).cutoff == 1500
    assert repo.current_file(meta, 9999).cutoff == 1760


def test_default_metagame_is_newest(chaos_dir):
    # regmb newer than regma; champions and base vgc are different game keys.
    assert ChaosRepository(chaos_dir).default_metagame() == "gen9championsvgc2026regmb"


def test_reg_fallback_stays_in_same_game(chaos_dir):
    repo = ChaosRepository(chaos_dir)
    fallbacks = repo.reg_fallback_files("gen9championsvgc2026regmb")
    metas = {f.metagame for f in fallbacks}
    assert "gen9championsvgc2026regma" in metas
    assert "gen9vgc2026regmb" not in metas  # different game — excluded


def test_species_resolves_via_reg_fallback(chaos_dir):
    adapter = ChaosAdapter(chaos_dir)
    meta = "gen9championsvgc2026regmb"
    # Ogerpon is only in the previous regulation.
    summary = adapter.get_pokemon_summary("Ogerpon", metagame=meta)
    assert summary.top_moves
    assert "(fallback)" in summary.source
    assert "regma" in summary.source


def test_build_match_context_sets_tiers(chaos_dir):
    ctx = ChaosAdapter(chaos_dir).build_match_context(
        ["Chi-Yu"], metagame="gen9championsvgc2026regmb", rating=1206
    )
    assert "@1760" in ctx.pokemon_stats["Chi-Yu"].source
    assert "current tier" in ctx.rating_note
    assert ctx.current_tier_stats  # bracket differs from ideal


def test_strategy_uses_reg_fallback(chaos_dir):
    strat = ChaosStrategyAdapter(chaos_dir).get_strategy(
        "Ogerpon", metagame="gen9championsvgc2026regmb"
    )
    assert "regma" in strat.overview and "fallback" in strat.overview
