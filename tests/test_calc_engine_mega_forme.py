"""Regression: the calc engine must use a Pokemon's OBSERVED in-battle forme
(e.g. Mega Evolution) when its installed dex has real data for it, instead of
always computing on base stats.

Reported bug: the parser deliberately keeps a Mega-Evolved Pokemon's stable
roster identity in PokemonSet.species (see showdown_parser.record_forme) and
records the observed forme separately in `battle_formes` — but the calc
adapter never looked at `battle_formes` at all, so every calc for a Mega'd
Pokemon silently used base stats regardless of whether the calc engine's own
dex actually had the Mega's data. This is an integration test against the
REAL Node subprocess (not the in-memory fake), because the bug is in what
species string reaches the real @smogon/calc library, which a fake can't
catch.
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


def test_observed_mega_forme_produces_different_stats_than_base(real_calc):
    """Mega Staraptor's real Atk/Spe differ from base Staraptor's, so the
    projected damage MUST differ once the forme is actually used."""
    defender = PokemonSet(species="Sinistcha", level=50, evs={"hp": 252, "def": 252})

    base = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Staraptor", level=50, item="Life Orb"),
            defender=defender,
            move="Brave Bird",
            field={},
        )
    )
    mega = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(
                species="Staraptor", level=50, item="Life Orb",
                battle_formes=["Staraptor-Mega"],
            ),
            defender=defender,
            move="Brave Bird",
            field={},
        )
    )
    assert mega.max_percent > base.max_percent
    assert "Staraptor-Mega" in mega.description


def test_forme_resolves_reports_a_known_and_an_unknown_forme(real_calc):
    assert real_calc.forme_resolves(9, "Staraptor-Mega") is True
    assert real_calc.forme_resolves(9, "Staraptor-TotallyMadeUpForme") is False


def test_unresolvable_battle_forme_falls_back_to_base_stats_safely(real_calc):
    """A forme this installed version's dex genuinely doesn't have must not
    crash the calc — it silently computes on base stats exactly as before."""
    result = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(
                species="Staraptor", level=50,
                battle_formes=["Staraptor-TotallyMadeUpForme"],
            ),
            defender=PokemonSet(species="Sinistcha", level=50),
            move="Brave Bird",
            field={},
        )
    )
    assert result.max_percent > 0
    assert "Staraptor-TotallyMadeUpForme" not in result.description
