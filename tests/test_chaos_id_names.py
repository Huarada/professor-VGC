"""Regression tests: abilities/items/natures from usage data reach the calc.

Smogon Chaos dumps name abilities, items and natures by id ("lifeorb",
"scrappy", "friendguard"); battle logs use display names ("Life Orb"). The
calc engine only matched display names and silently ignored the rest, so
every ability/item back-filled from usage data dropped out of every calc
(projections, alternatives, threats, verdicts), and the Python side never
recognized a back-filled Friend Guard or Choice Scarf. The bundled
``sample_data`` fixture uses display names, which is why no test caught it;
these tests use the real id format.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.chaos_repository import ChaosRepository
from src.domain.exceptions import CalcEngineError
from src.domain.models import (
    CalcRequest,
    MetaContext,
    PokemonMetaSummary,
    PokemonSet,
    SelectionPlan,
)
from src.services.matchup_evaluator import MatchupEvaluator
from src.services.turn_simulator import TurnReplaySimulator
from tests.test_battle_state_per_move import _parse, _RecordingCalc

_NODE_CALC_DIR = Path(__file__).resolve().parent.parent / "node_calc"
_FORMAT = "gen9championsvgc2026regmb"


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


def _calc(calc: SmogonCalcAdapter, attacker: PokemonSet, defender: str, move: str):
    return calc.calculate(
        CalcRequest(attacker=attacker, defender=PokemonSet(species=defender), move=move)
    )


@pytest.mark.parametrize(
    ("species", "display", "chaos_id", "move", "defender"),
    [
        ("Decidueye-Hisui", {"ability": "Scrappy"}, {"ability": "scrappy"}, "Close Combat", "Froslass"),
        ("Primarina", {"ability": "Liquid Voice"}, {"ability": "liquidvoice"}, "Hyper Voice", "Gengar"),
        ("Garchomp", {"item": "Life Orb"}, {"item": "lifeorb"}, "Dragon Claw", "Amoonguss"),
        ("Garchomp", {"item": "Choice Band"}, {"item": "choiceband"}, "Dragon Claw", "Amoonguss"),
        ("Garchomp", {"nature": "Adamant"}, {"nature": "adamant"}, "Dragon Claw", "Amoonguss"),
    ],
)
def test_chaos_ids_count_exactly_like_display_names(
    real_calc, species, display, chaos_id, move, defender
):
    named = _calc(real_calc, PokemonSet(species=species, **display), defender, move)
    by_id = _calc(real_calc, PokemonSet(species=species, **chaos_id), defender, move)
    bare = _calc(real_calc, PokemonSet(species=species), defender, move)
    assert by_id.damage_rolls == named.damage_rolls
    assert by_id.description == named.description
    assert by_id.max_percent != bare.max_percent  # the ability/item/nature really counts


def test_no_item_and_no_ability_ids_mean_none(real_calc):
    bare = _calc(real_calc, PokemonSet(species="Garchomp"), "Amoonguss", "Dragon Claw")
    for kwargs in ({"item": "nothing"}, {"ability": "noability"}):
        result = _calc(real_calc, PokemonSet(species="Garchomp", **kwargs), "Amoonguss", "Dragon Claw")
        assert result.damage_rolls == bare.damage_rolls


def test_back_filled_choice_scarf_counts_for_speed(real_calc):
    scarfed = real_calc.compare_speed(
        CalcRequest(
            attacker=PokemonSet(species="Garchomp", item="choicescarf"),
            defender=PokemonSet(species="Dragapult"),
            move="Tackle",
        )
    )
    assert scarfed.faster == "Garchomp"
    assert "Choice Scarf (Garchomp)" in scarfed.conditions


@pytest.mark.parametrize("base_ability", ["Trace", "trace", None])
def test_mega_forme_uses_its_own_ability(real_calc, base_ability):
    """A Mega has exactly one ability: Gardevoir-Mega is Pixilate whatever the
    base form revealed (Trace) or usage data back-filled ("trace")."""
    mega = PokemonSet(
        species="Gardevoir", ability=base_ability, battle_formes=["Gardevoir-Mega"]
    )
    result = _calc(real_calc, mega, "Sinistcha-Masterpiece", "Hyper Voice")
    assert "Pixilate" in result.description
    assert result.max_percent > 0  # Fairy-type Hyper Voice hits the Ghost type


def _write_chaos(directory: Path, data: dict) -> None:
    """A Chaos dump in Smogon's real format: abilities/items/spreads by id."""
    payload = {"info": {"metagame": _FORMAT, "cutoff": 1760}, "data": data}
    (directory / f"{_FORMAT}-1760.json").write_text(json.dumps(payload), encoding="utf-8")


def test_usage_data_back_fill_reaches_the_verdict(real_calc, tmp_path):
    """End to end: a Chaos dump with ids -> back-fill -> real engine."""
    _write_chaos(tmp_path, {
        "Garchomp": {
            "Raw count": 1000,
            "Abilities": {"roughskin": 900, "sandveil": 100},
            "Items": {"lifeorb": 800, "nothing": 200},
            "Moves": {"dragonclaw": 1000},
            "Spreads": {"Adamant:2/32/0/0/0/32": 1000},  # Champions stat points
        },
        "Sinistcha": {
            "Raw count": 1000,
            "Abilities": {"hospitality": 1000},
            "Items": {"nothing": 1000},
            "Moves": {"matchagotcha": 1000},
            "Spreads": {"Relaxed:32/0/32/0/2/0": 1000},
        },
    })
    meta = ChaosAdapter(repository=ChaosRepository(tmp_path)).build_match_context(
        ["Garchomp", "Sinistcha"], metagame=_FORMAT
    )
    state = _parse(
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Sinistcha|Sinistcha, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Sinistcha",
        "|-damage|p2a: Sinistcha|40/100",
    )
    verdicts = MatchupEvaluator(real_calc).evaluate(
        state, SelectionPlan(matchups=[("Garchomp", "Sinistcha")]), meta
    )
    assert len(verdicts) == 1
    description = verdicts[0].best_damage.description
    assert "Life Orb Garchomp" in description  # the back-filled item was applied
    assert "32+ Atk" in description  # Champions' back-filled Adamant stat points


def test_back_filled_friend_guard_is_recognized():
    """The ally's Friend Guard comes from usage data as "friendguard"."""
    state = _parse(
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Amoonguss|Amoonguss, L50|100/100",
        "|switch|p2b: Clefairy|Clefairy, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Amoonguss",
        "|-damage|p2a: Amoonguss|70/100",
    )
    meta = MetaContext(
        pokemon_stats={"Clefairy": PokemonMetaSummary(top_abilities={"friendguard": 0.95})}
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, meta)
    request = calc.damage_requests("Garchomp", "Dragon Claw", "Amoonguss")[0]
    assert request.field.defender_side.friend_guard is True
