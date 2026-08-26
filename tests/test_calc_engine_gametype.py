"""Regression: the calc engine must run VGC (always-Doubles) calculations.

Reported bug: the Node calc engine never told @smogon/calc this is a Doubles
battle, so it defaulted to Singles — meaning the standard 0.75x spread-move
damage reduction (applied whenever 2+ Pokemon are hit) was never applied.
Every spread move (Earthquake, Rock Slide, Heat Wave, ...) in this VGC-only
project was overstated by ~33%. This is an integration test against the REAL
Node subprocess (not the in-memory fake) because the bug is specifically in
how the Node side builds the @smogon/calc Field, which a fake can't catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.domain.exceptions import CalcEngineError
from src.domain.models import CalcRequest, PokemonSet

_NODE_CALC_DIR = Path(__file__).resolve().parent.parent / "node_calc"


@pytest.fixture(scope="module")
def real_calc():
    try:
        adapter = SmogonCalcAdapter(
            server_script=_NODE_CALC_DIR / "calc_server.js", gen=9, timeout_seconds=10
        )
        adapter.calculate(
            CalcRequest(
                gen=9,
                attacker=PokemonSet(species="Garchomp", level=50),
                defender=PokemonSet(species="Ceruledge", level=50),
                move="Tackle",
                field={},
            )
        )
    except CalcEngineError as exc:  # pragma: no cover - environment without node
        pytest.skip(f"Node calc engine unavailable: {exc}")
    yield adapter
    adapter.close()


def test_spread_move_gets_the_doubles_reduction(real_calc):
    result = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Garchomp", level=50),
            defender=PokemonSet(species="Ceruledge", level=50),
            move="Earthquake",
            field={},
        )
    )
    # Singles (the old, wrong default) gives 113.3-136% (guaranteed OHKO).
    # Real Doubles applies the 0.75x spread reduction: ~85.3-101.3%.
    assert result.max_percent < 110.0
    assert result.is_ko_guaranteed is False


def test_single_target_move_is_unaffected_by_the_doubles_fix(real_calc):
    result = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Raichu", level=50),
            defender=PokemonSet(species="Basculegion", level=50),
            move="Zap Cannon",  # not a spread move — no 2+ targets to reduce for
            field={},
        )
    )
    assert 80.0 <= result.min_percent <= 82.0
    assert 95.0 <= result.max_percent <= 97.0
