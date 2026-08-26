"""Integration test against the REAL Node subprocess (not the in-memory
fake): confirms `PokemonSet.boosts` actually reaches @smogon/calc through
the full Python -> IPC -> Node -> @smogon/calc chain and changes its real
output — a fake can't catch a break anywhere in that chain (the Python
payload builder, calc_server.js's transport, or calcEngine.js's
buildPokemon options mapping).
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


def test_attacker_negative_boost_reduces_real_projected_damage(real_calc):
    neutral = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Garchomp", level=50, nature="Jolly", evs={"atk": 252, "spe": 252}),
            defender=PokemonSet(species="Whimsicott", level=50),
            move="Earthquake",
            field={},
        )
    )
    intimidated = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(
                species="Garchomp", level=50, nature="Jolly", evs={"atk": 252, "spe": 252},
                boosts={"atk": -1},
            ),
            defender=PokemonSet(species="Whimsicott", level=50),
            move="Earthquake",
            field={},
        )
    )
    assert intimidated.max_percent < neutral.max_percent
    assert intimidated.min_percent < neutral.min_percent


def test_defender_positive_boost_reduces_real_incoming_damage(real_calc):
    neutral = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Garchomp", level=50, nature="Jolly", evs={"atk": 252}),
            defender=PokemonSet(species="Whimsicott", level=50),
            move="Earthquake",
            field={},
        )
    )
    defended = real_calc.calculate(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Garchomp", level=50, nature="Jolly", evs={"atk": 252}),
            defender=PokemonSet(species="Whimsicott", level=50, boosts={"def": 2}),
            move="Earthquake",
            field={},
        )
    )
    assert defended.max_percent < neutral.max_percent


def _garchomp_speed(comparison, attacker_species: str = "Garchomp") -> int:
    """SpeedComparison reports faster/slower roles, not fixed attacker/
    defender ones — pick out whichever side is actually Garchomp."""
    if comparison.faster == attacker_species:
        return comparison.faster_speed
    return comparison.slower_speed


def test_speed_boost_changes_real_effective_speed(real_calc):
    neutral = real_calc.compare_speed(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(species="Garchomp", level=50, evs={"spe": 252}),
            defender=PokemonSet(species="Whimsicott", level=50),
            move="Tackle",
            field={},
        )
    )
    boosted = real_calc.compare_speed(
        CalcRequest(
            gen=9,
            attacker=PokemonSet(
                species="Garchomp", level=50, evs={"spe": 252}, boosts={"spe": 2},
            ),
            defender=PokemonSet(species="Whimsicott", level=50),
            move="Tackle",
            field={},
        )
    )
    # A +2 stage is a clean x2 multiplier on the real stat.
    assert _garchomp_speed(boosted) == _garchomp_speed(neutral) * 2
