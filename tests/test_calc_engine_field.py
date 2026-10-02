"""Integration tests: field state, current HP and move data reach the REAL engine.

Reported/found bugs these pin down against the real Node subprocess (a fake
can't catch how the Node side builds the @smogon/calc ``Field``):

- weather was sent as the Showdown protocol id ("SunnyDay"), which
  @smogon/calc silently ignores — weather never changed a single calc;
- terrain, Reflect/Light Screen/Aurora Veil, Helping Hand and Friend Guard
  never reached the engine at all;
- the KO-chance text always assumed a full-HP target;
- "is this a status move?" came from a hand-kept, incomplete list instead of
  the engine's own move data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.domain.exceptions import CalcEngineError
from src.domain.models import CalcField, CalcRequest, PokemonSet, SideField

_NODE_CALC_DIR = Path(__file__).resolve().parent.parent / "node_calc"


@pytest.fixture(scope="module")
def real_calc():
    try:
        adapter = SmogonCalcAdapter(
            server_script=_NODE_CALC_DIR / "calc_server.js", gen=9, timeout_seconds=10
        )
        adapter.move_info(9, "Tackle")
    except CalcEngineError as exc:  # pragma: no cover - environment without node
        pytest.skip(f"Node calc engine unavailable: {exc}")
    yield adapter
    adapter.close()


def _max(calc: SmogonCalcAdapter, attacker: str, defender: str, move: str, **kwargs) -> float:
    return calc.calculate(
        CalcRequest(
            attacker=PokemonSet(species=attacker, nature="Modest", evs={"spa": 252, "atk": 252}),
            defender=PokemonSet(species=defender),
            move=move,
            **kwargs,
        )
    ).max_percent


def test_weather_changes_damage(real_calc):
    plain = _max(real_calc, "Charizard", "Garchomp", "Heat Wave")
    sun = _max(real_calc, "Charizard", "Garchomp", "Heat Wave", field=CalcField(weather="Sun"))
    rain = _max(real_calc, "Charizard", "Garchomp", "Heat Wave", field=CalcField(weather="Rain"))
    assert sun > plain * 1.4
    assert rain < plain * 0.6


def test_terrain_and_defensive_side_conditions_change_damage(real_calc):
    plain = _max(real_calc, "Raichu", "Amoonguss", "Thunderbolt")
    assert _max(
        real_calc, "Raichu", "Amoonguss", "Thunderbolt", field=CalcField(terrain="Electric")
    ) > plain * 1.2
    for side in (
        SideField(light_screen=True), SideField(aurora_veil=True), SideField(friend_guard=True)
    ):
        assert _max(
            real_calc, "Raichu", "Amoonguss", "Thunderbolt",
            field=CalcField(defender_side=side),
        ) < plain * 0.8
    physical = _max(real_calc, "Garchomp", "Amoonguss", "Dragon Claw")
    assert _max(
        real_calc, "Garchomp", "Amoonguss", "Dragon Claw",
        field=CalcField(defender_side=SideField(reflect=True)),
    ) < physical * 0.8


def test_helping_hand_boosts_the_attacker(real_calc):
    plain = _max(real_calc, "Garchomp", "Amoonguss", "Dragon Claw")
    helped = _max(
        real_calc, "Garchomp", "Amoonguss", "Dragon Claw",
        field=CalcField(attacker_side=SideField(helping_hand=True)),
    )
    assert helped > plain * 1.4


def test_ko_chance_uses_the_defenders_current_hp(real_calc):
    request = CalcRequest(
        attacker=PokemonSet(species="Garchomp", nature="Jolly", evs={"atk": 252}),
        defender=PokemonSet(species="Garchomp"),
        move="Dragon Claw",
    )
    full = real_calc.calculate(request)
    hurt = real_calc.calculate(request.model_copy(update={"defender_hp_percent": 50.0}))
    assert "OHKO" not in full.ko_chance_text
    assert "OHKO" in hurt.ko_chance_text
    assert hurt.max_percent == full.max_percent  # damage is still % of MAX HP


def test_move_info_comes_from_the_engine(real_calc):
    protect = real_calc.move_info(9, "Protect")
    assert protect.category == "Status" and protect.is_protect and not protect.is_damaging
    fake_out = real_calc.move_info(9, "Fake Out")
    assert fake_out.is_damaging and not fake_out.is_protect
    icy_wind = real_calc.move_info(9, "Icy Wind")
    assert icy_wind.is_spread and icy_wind.speed_control == "speed_drop"
    assert icy_wind.speed_drop_stages == 1
    assert real_calc.move_info(9, "Tailwind").speed_control == "tailwind"
    assert real_calc.move_info(9, "Trick Room").speed_control == "trick_room"
    assert real_calc.move_info(9, "Thunder Wave").speed_control == "paralysis"
    unknown = real_calc.move_info(9, "Not A Real Move")
    assert not unknown.known and unknown.is_damaging  # the engine itself will judge it
