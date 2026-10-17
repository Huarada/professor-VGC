"""Pokemon Champions formats are calculated under Champions' own rules.

@smogon/calc implements Champions as generation 0: stat points (0-32 per
stat) instead of EVs, and Champions-only damage rules such as Mega Sol. The
project used to calculate every Champions game with Scarlet/Violet rules
(generation 9) — stats agreed thanks to the stat-point -> EV conversion, the
damage rules did not (Meganium-Mega's Mega Sol hit 45-54% of a Tyranitar in
sand where the real game showed 74%). A generation of 0 was also silently
swallowed by ``gen or default`` / ``gen || 9`` on both sides of the IPC.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.domain.exceptions import CalcEngineError
from src.domain.models import (
    CalcField,
    CalcRequest,
    MetaContext,
    PokemonSet,
    SelectionPlan,
    StatSpread,
)
from src.domain.regulation import CHAMPIONS_CALC_GEN, calc_generation
from src.services.matchup_evaluator import MatchupEvaluator
from src.services.turn_simulator import TurnReplaySimulator
from tests.test_battle_state_per_move import _parse, _RecordingCalc

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


@pytest.mark.parametrize(
    ("format_id", "expected"),
    [
        ("gen9championsvgc2026regmb", CHAMPIONS_CALC_GEN),
        ("gen9championsvgc2026regmcbo3", CHAMPIONS_CALC_GEN),
        ("gen9championsbssregmb", CHAMPIONS_CALC_GEN),
        ("gen9vgc2025regi", 9),
        (None, 9),
    ],
)
def test_calc_generation_follows_the_format(format_id, expected):
    assert calc_generation(format_id, 9) == expected


def _game(*tier: str):
    return _parse(
        *tier,
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Incineroar|Incineroar, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        "|-damage|p2a: Incineroar|70/100",
    )


@pytest.mark.parametrize(
    ("tier", "gen"),
    [("|tier|[Gen 9 Champions] VGC 2026 Reg M-B", 0), ("|tier|[Gen 9] VGC 2025 Reg I", 9)],
)
def test_every_calc_of_a_game_uses_its_formats_rules(tier, gen):
    state = _game(tier)
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    MatchupEvaluator(calc).evaluate(
        state, SelectionPlan(matchups=[("Garchomp", "Incineroar")]), MetaContext()
    )
    assert calc.requests
    assert {request.gen for request in calc.requests} == {gen}


def _damage(calc, gen, attacker, defender, move, **kw):
    return calc.calculate(CalcRequest(gen=gen, attacker=attacker, defender=defender, move=move, **kw))


def test_mega_sol_follows_champions_rules(real_calc):
    """Mega Sol: the attack acts as if in sun, so sand's Rock Sp. Def boost is gone."""
    mega = PokemonSet(species="Meganium", ability="Mega Sol", battle_formes=["Meganium-Mega"],
                      nature="Modest", evs={"hp": 256, "spa": 256})
    ttar = PokemonSet(species="Tyranitar", nature="Jolly", evs={"hp": 16, "atk": 256, "spe": 256})
    sand = CalcField(weather="Sand")
    sv = _damage(real_calc, 9, mega, ttar, "Dazzling Gleam", field=sand)
    champions = _damage(real_calc, 0, mega, ttar, "Dazzling Gleam", field=sand)
    assert champions.max_percent > sv.max_percent * 1.4
    assert 74.0 <= champions.max_percent  # the real game: 74% observed


def test_spreads_become_stat_points(real_calc):
    """The EV-scale spread (usage data's stat points x 8) is converted back:
    32 stat points, from 256 or 252, are the same investment."""
    chomp = PokemonSet(species="Garchomp", nature="Adamant", evs={"atk": 256})
    incin = PokemonSet(species="Incineroar")
    full = _damage(real_calc, 0, chomp, incin, "Dragon Claw")
    capped = _damage(real_calc, 0, chomp.model_copy(update={"evs": StatSpread(atk=252)}), incin, "Dragon Claw")
    bare = _damage(real_calc, 0, chomp.model_copy(update={"evs": None}), incin, "Dragon Claw")
    assert full.damage_rolls == capped.damage_rolls
    assert "32+ Atk" in full.description
    assert full.max_percent > bare.max_percent


def test_a_pokemon_missing_from_the_champions_data_falls_back_and_says_so(real_calc):
    chomp = PokemonSet(species="Garchomp")
    result = _damage(real_calc, 0, chomp, PokemonSet(species="Amoonguss"), "Dragon Claw")
    assert result.max_percent > 0
    assert result.description.endswith(
        "[Scarlet/Violet rules: Amoonguss not in the Pokemon Champions data]"
    )
    speed = real_calc.compare_speed(CalcRequest(
        gen=0, attacker=chomp, defender=PokemonSet(species="Amoonguss"), move="Dragon Claw"
    ))
    assert speed.faster == "Garchomp"


def test_move_data_under_champions_rules(real_calc):
    quake = real_calc.move_info(0, "Earthquake")
    assert quake.known and quake.category == "Physical" and quake.is_spread
    assert real_calc.move_info(0, "Protect").is_protect
    assert real_calc.move_info(0, "Tailwind").speed_control == "tailwind"
