"""Regression tests for the engine-calibration misses NO EV/nature spread
explained: each one traced to a bug, pinned here.

- Illusion (Zoroark): the disguise's moves, damage and faint landed on the
  Pokemon it imitated (which then "acted after fainting");
- a nicknamed Pokemon switching back in as a Mega became a 7th team member;
- the forme seen LATER in the game applied to every move (a Mega before it
  evolved, Mimikyu-Busted while its Disguise held, a Transform target);
- Transform (Imposter Ditto) never changed the Pokemon's stats or ability;
- the attacker's current HP and fainted allies never reached the engine
  (Eruption at full power, Last Respects always 50 BP, no Supreme Overlord);
- moves @smogon/calc cannot project were reported as "0% (immune)";
- usage data for a species absent from the top bracket, or for a Mega under
  its own name, was never found;
- a back-filled resist berry the log shows was not eaten still halved the hit;
- the EV envelope was computed on a different request than the projection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.faithfulness_benchmark.ev_envelope import RecordingCalc
from scripts.faithfulness_benchmark.observed_damage import ObservedHit, extract_observed_hits
from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.chaos_repository import ChaosRepository, StrictRegulationRepository
from src.adapters.parsers.showdown_log.illusion import unmask_illusions
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.exceptions import CalcEngineError
from src.domain.models import (
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    PokemonMetaSummary,
    PokemonSet,
)
from src.domain.names import to_id
from src.services.battle_context import context_species
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


def _team(state: GameState, player: str) -> list[str]:
    side = next(s for s in state.sides if s.player == player)
    return [m.species for m in side.team]


def _moves(state: GameState) -> list:
    return [e for e in state.outcome.events if e.kind == "move"]


# -- Illusion ----------------------------------------------------------------- #

_PREVIEW = [
    "|poke|p2|Garchomp, L50, M|", "|poke|p2|Hydreigon, L50|", "|poke|p2|Zoroark-Hisui, L50, M|",
]


def test_broken_illusion_is_the_illusion_user_not_the_disguise():
    """Line shapes from a real game: the "Garchomp" that fainted was Zoroark."""
    state = _parse(
        *_PREVIEW,
        "|switch|p1a: Excadrill|Excadrill, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|turn|1",
        "|move|p2a: Garchomp|Bitter Malice|p1a: Excadrill",
        "|-damage|p1a: Excadrill|70/100",
        "|move|p1a: Excadrill|High Horsepower|p2a: Garchomp",
        "|-damage|p2a: Garchomp|0 fnt",
        "|replace|p2a: Zoroark|Zoroark-Hisui, L50, M",
        "|-end|p2a: Zoroark|Illusion",
        "|faint|p2a: Zoroark",
        "|turn|2",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|move|p2a: Garchomp|Earthquake|p1a: Excadrill",
        "|-damage|p1a: Excadrill|10/100",
    )
    assert [(k.player, k.fainted) for k in state.outcome.kos] == [("p2", "Zoroark-Hisui")]
    bitter, horsepower, quake = _moves(state)
    assert (bitter.actor_player, bitter.actor) == ("p2", "Zoroark-Hisui")
    assert horsepower.hits[0].species == "Zoroark-Hisui"
    assert "Garchomp" not in quake.state.fainted.get("p2", [])  # the real one is alive
    assert any(
        e.text == "p2 sent out Zoroark-Hisui (disguised as Garchomp by Illusion)."
        for e in state.outcome.events
    )


def test_unbroken_illusion_is_caught_when_the_same_pokemon_enters_twice():
    state = _parse(
        "|poke|p1|Alakazam, L50|", "|poke|p1|Zoroark-Hisui, L50, M|",
        "|switch|p1a: Alakazam|Alakazam, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|turn|1",
        "|switch|p1b: Alakazam|Alakazam, L50|100/100",
        "|move|p1b: Alakazam|Shadow Ball|p2a: Garchomp",
        "|-damage|p2a: Garchomp|60/100",
    )
    shadow_ball = _moves(state)[0]
    assert shadow_ball.actor == "Zoroark-Hisui"
    assert shadow_ball.state.active["p1"] == ["Alakazam", "Zoroark-Hisui"]


def test_a_later_replace_in_the_other_slot_names_the_illusion_user():
    """The first "Skeledirge" was the Zoroark (revealed later), not the newcomer."""
    text = unmask_illusions("\n".join([
        "|poke|p1|Skeledirge, L50|", "|poke|p1|Zoroark-Hisui, L50, F|",
        "|switch|p1b: Skeledirge|Skeledirge, L50|100/100",
        "|switch|p1a: Skeledirge|Skeledirge, L50|100/100",
        "|-damage|p1b: Skeledirge|1/100",
        "|replace|p1b: Zoroark|Zoroark-Hisui, L50, F",
    ]))
    lines = text.splitlines()
    assert lines[2].startswith("|switch|p1b: Zoroark|Zoroark-Hisui")
    assert lines[3] == "|switch|p1a: Skeledirge|Skeledirge, L50|100/100"
    assert lines[4] == "|-damage|p1b: Zoroark|1/100"


# -- identity and formes ---------------------------------------------------- #


def test_nicknamed_mega_switching_back_in_keeps_its_identity():
    state = _parse(
        "|poke|p1|Dragonite, L50, M|",
        "|switch|p1a: Dragon Deez|Dragonite, L50, M|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|turn|1",
        "|detailschange|p1a: Dragon Deez|Dragonite-Mega, L50, M",
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|turn|2",
        "|switch|p1a: Dragon Deez|Dragonite-Mega, L50, M|40/100",
    )
    assert _team(state, "p1") == ["Dragonite", "Garchomp"]


def test_a_forme_applies_only_from_when_it_happened():
    state = _parse(
        "|switch|p1a: Gardevoir|Gardevoir, L50|100/100",
        "|switch|p2a: Mimikyu|Mimikyu, L50|100/100",
        "|turn|1",
        "|move|p1a: Gardevoir|Moonblast|p2a: Mimikyu",
        "|-activate|p2a: Mimikyu|ability: Disguise",
        "|detailschange|p2a: Mimikyu|Mimikyu-Busted, L50",
        "|turn|2",
        "|detailschange|p1a: Gardevoir|Gardevoir-Mega, L50",
        "|move|p1a: Gardevoir|Moonblast|p2a: Mimikyu",
        "|-damage|p2a: Mimikyu|20/100",
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    first, second = calc.damage_requests("Gardevoir", "Moonblast", "Mimikyu")
    assert first.attacker.battle_formes == [] and first.defender.battle_formes == []
    assert second.attacker.battle_formes == ["Gardevoir-Mega"]
    assert second.defender.battle_formes == ["Mimikyu-Busted"]


def test_transform_takes_the_targets_species_and_ability():
    state = _parse(
        "|switch|p1a: Mawile|Mawile, L50|100/100",
        "|switch|p2a: Ditto|Ditto, L50|100/100",
        "|-transform|p2a: Ditto|p1a: Mawile|[from] ability: Imposter",
        "|turn|1",
        "|move|p2a: Ditto|Play Rough|p1a: Mawile",
        "|-damage|p1a: Mawile|50/100",
    )
    meta = MetaContext(pokemon_stats={
        "Mawile": PokemonMetaSummary(top_abilities={"hugepower": 0.9}),
        "Ditto": PokemonMetaSummary(top_abilities={"imposter": 1.0}, top_items={"choicescarf": 1.0}),
    })
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, meta)
    ditto = calc.damage_requests("Ditto", "Play Rough", "Mawile")[0].attacker
    assert ditto.battle_formes == ["Mawile"]
    assert ditto.ability == "hugepower"
    assert ditto.item == "choicescarf"  # its own item, not the target's


def test_attacker_hp_and_fainted_allies_reach_the_request():
    state = _parse(
        "|switch|p1a: Torkoal|Torkoal, L50|100/100",
        "|switch|p1b: Amoonguss|Amoonguss, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|turn|1",
        "|move|p2a: Garchomp|Earthquake|p1a: Torkoal",
        "|-damage|p1a: Torkoal|40/100",
        "|-damage|p1b: Amoonguss|0 fnt",
        "|faint|p1b: Amoonguss",
        "|turn|2",
        "|move|p1a: Torkoal|Eruption|p2a: Garchomp",
        "|-damage|p2a: Garchomp|80/100",
    )
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, MetaContext())
    eruption = calc.damage_requests("Torkoal", "Eruption", "Garchomp")[-1]  # turn 2's own
    assert eruption.attacker_hp_percent == 40.0
    assert eruption.attacker_allies_fainted == 1


# -- the real engine ---------------------------------------------------------- #


def _dmg(calc, attacker: PokemonSet, defender: PokemonSet, move: str, **kw) -> DamageResult:
    return calc.calculate(CalcRequest(attacker=attacker, defender=defender, move=move, **kw))


def test_hp_and_fainted_allies_change_the_engine_result(real_calc):
    tork, chomp = PokemonSet(species="Torkoal"), PokemonSet(species="Garchomp")
    full = _dmg(real_calc, tork, chomp, "Eruption")
    low = _dmg(real_calc, tork, chomp, "Eruption", attacker_hp_percent=33.0)
    assert low.max_percent < full.max_percent * 0.4
    basc = PokemonSet(species="Basculegion", ability="Adaptability")
    none = _dmg(real_calc, basc, chomp, "Last Respects")
    three = _dmg(real_calc, basc, chomp, "Last Respects", attacker_allies_fainted=3)
    assert three.max_percent > none.max_percent * 3.5  # 200 BP vs 50 BP
    assert "Last Respects (200 BP)" in three.description
    gambit = PokemonSet(species="Kingambit", ability="Supreme Overlord")
    assert _dmg(real_calc, gambit, chomp, "Kowtow Cleave", attacker_allies_fainted=2).max_percent > (
        _dmg(real_calc, gambit, chomp, "Kowtow Cleave").max_percent
    )


def test_moves_the_calc_cannot_project_are_not_reported_immune(real_calc):
    fang = _dmg(real_calc, PokemonSet(species="Maushold"), PokemonSet(species="Raichu"),
                "Super Fang", defender_hp_percent=60.0)
    assert fang.max_percent == pytest.approx(30.0, abs=1.0)  # half of the CURRENT HP
    assert "immune" in _dmg(real_calc, PokemonSet(species="Maushold"),
                            PokemonSet(species="Gengar"), "Super Fang").description
    with pytest.raises(CalcEngineError, match="not projected"):
        _dmg(real_calc, PokemonSet(species="Swampert"), PokemonSet(species="Delphox"), "Mirror Coat")


def test_an_intact_disguise_absorbs_the_hit(real_calc):
    torterra, mimikyu = PokemonSet(species="Torterra"), PokemonSet(species="Mimikyu")
    assert _dmg(real_calc, torterra, mimikyu, "Wood Hammer").max_percent == 0.0
    busted = mimikyu.model_copy(update={"battle_formes": ["Mimikyu-Busted"]})
    assert _dmg(real_calc, torterra, busted, "Wood Hammer").max_percent > 0
    breaker = PokemonSet(species="Excadrill", ability="Mold Breaker")
    assert _dmg(real_calc, breaker, mimikyu, "Iron Head").max_percent > 0


# -- usage data --------------------------------------------------------------- #


def _write_tier(directory: Path, cutoff: int, data: dict) -> None:
    payload = {"info": {"metagame": _FORMAT, "cutoff": cutoff}, "data": data}
    (directory / f"{_FORMAT}-{cutoff}.json").write_text(json.dumps(payload), encoding="utf-8")


def _mon(ability: str, item: str) -> dict:
    return {"Raw count": 10, "Abilities": {ability: 10}, "Items": {item: 10},
            "Moves": {"tackle": 10}, "Spreads": {"Adamant:2/32/0/0/0/32": 10}}


def test_usage_lookup_falls_back_to_lower_tiers_and_finds_megas(tmp_path):
    _write_tier(tmp_path, 1760, {"Garchomp": _mon("roughskin", "lifeorb"),
                                 "Meganium-Mega": _mon("megasol", "meganiumite")})
    _write_tier(tmp_path, 1500, {"Diggersby": _mon("hugepower", "choiceband")})
    repo = ChaosRepository(tmp_path)
    for view in (repo, StrictRegulationRepository(repo)):  # same regulation: allowed when pinned
        meta = ChaosAdapter(repository=view).build_match_context(
            ["Diggersby", "Meganium-Mega"], metagame=_FORMAT
        )
        assert list(meta.pokemon_stats["Diggersby"].top_abilities) == ["hugepower"]
        assert meta.pokemon_stats["Diggersby"].source == f"{_FORMAT}@1500 (lower tier)"
        assert list(meta.pokemon_stats["Meganium-Mega"].top_abilities) == ["megasol"]
    state = ShowdownReplayParser().parse("\n".join([
        "|player|p1|Ash|", "|player|p2|Gary|",
        "|switch|p1a: Meganium|Meganium, L50|100/100",
        "|detailschange|p1a: Meganium|Meganium-Mega, L50",
    ]))
    assert "Meganium-Mega" in context_species(state)


# -- log contradicts a back-filled item -------------------------------------- #


class _BerryCalc(_RecordingCalc):
    """The resist berry shows up in the description whenever it is held."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        self.requests.append(request)
        berry = to_id(request.defender.item) == "chopleberry"
        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, min_percent=20.0 if berry else 40.0,
            max_percent=24.0 if berry else 48.0,
            description=f"{request.attacker.species} {request.move} vs. "
            + ("Chople Berry " if berry else "") + request.defender.species,
        )


def _low_kick(*after: str) -> GameState:
    return _parse(
        "|switch|p1a: Incineroar|Incineroar, L50|100/100",
        "|switch|p2a: Kingambit|Kingambit, L50|100/100",
        "|turn|1",
        "|move|p1a: Incineroar|Low Kick|p2a: Kingambit",
        *after,
    )


@pytest.mark.parametrize(("eaten", "projected_max"), [(False, 48.0), (True, 24.0)])
def test_a_back_filled_resist_berry_must_be_eaten(eaten, projected_max):
    lines = ["|-enditem|p2a: Kingambit|Chople Berry|[eat]"] if eaten else []
    state = _low_kick(*lines, "|-damage|p2a: Kingambit|60/100")
    meta = MetaContext(
        pokemon_stats={"Kingambit": PokemonMetaSummary(top_items={"chopleberry": 0.6})}
    )
    check = TurnReplaySimulator(_BerryCalc()).simulate(state, meta)[0]
    assert check.damage_checks[0].projected_max_percent == projected_max


# -- benchmark ------------------------------------------------------------------ #


class _DescribingEngine:
    def calculate(self, request: CalcRequest) -> DamageResult:
        return DamageResult(attacker=request.attacker.species, defender=request.defender.species,
                            move=request.move, description=f"boosts={request.attacker.boosts}")


def test_ev_envelope_uses_the_projections_own_request():
    """The same attacker/defender/move/HP is also calculated as an alternative
    at another moment; the projection's description picks the right one."""
    recorder = RecordingCalc(_DescribingEngine())  # type: ignore[arg-type]
    earlier = CalcRequest(attacker=PokemonSet(species="Garchomp"),
                          defender=PokemonSet(species="Amoonguss"), move="Dragon Claw",
                          defender_hp_percent=100.0)
    actual = earlier.model_copy(update={
        "attacker": PokemonSet(species="Garchomp", boosts={"atk": 2})
    })
    recorder.calculate(earlier)
    recorder.calculate(actual)
    hit = ObservedHit(turn=3, attacker_player="p1", attacker="Garchomp", move="Dragon Claw",
                      defender_player="p2", defender="Amoonguss", hp_before=100.0, hp_after=60.0)
    assert recorder.request_for(hit, "boosts={'atk': 2}") == actual
    assert recorder.request_for(hit) == earlier  # without a description: first one, as before


def test_the_independent_observed_reader_resolves_illusion_too():
    """The benchmark's own log reader (no product import) must agree on who was
    hit, or every hit on an Illusion user silently drops out as unmatched."""
    hits = extract_observed_hits("\n".join([
        *_PREVIEW,
        "|switch|p1a: Excadrill|Excadrill, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|turn|1",
        "|move|p1a: Excadrill|High Horsepower|p2a: Garchomp",
        "|-damage|p2a: Garchomp|0 fnt",
        "|replace|p2a: Zoroark|Zoroark-Hisui, L50, M",
        "|faint|p2a: Zoroark",
        "|turn|2",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|move|p1a: Excadrill|Iron Head|p2a: Garchomp",
        "|-damage|p2a: Garchomp|70/100",
    ]))
    assert [(h.defender, h.hp_before, h.hp_after) for h in hits] == [
        ("Zoroark-Hisui", 100.0, 0.0), ("Garchomp", 100.0, 70.0),
    ]


def test_abilities_named_by_a_from_suffix_are_revealed():
    """Protean, Rough Skin, Frisk, Solar Power... are revealed by "[from] ability:"
    on another line, not by "-ability": the usage-data guess (Overgrow) was used
    where the log had shown Protean (a missing STAT on a Triple Axel)."""
    state = _parse(
        "|switch|p1a: Meowscarada|Meowscarada, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2b: Torkoal|Torkoal, L50|100/100",
        "|turn|1",
        "|move|p1a: Meowscarada|Triple Axel|p2a: Garchomp",
        "|-start|p1a: Meowscarada|typechange|Ice|[from] ability: Protean",
        "|-damage|p2a: Garchomp|26/100",
        "|-damage|p1a: Meowscarada|50/100|[from] ability: Rough Skin|[of] p2a: Garchomp",
        "|-weather|SunnyDay|[from] ability: Drought|[of] p2b: Torkoal",
    )
    abilities = {m.species: m.ability for s in state.sides for m in s.team}
    assert abilities == {"Meowscarada": "Protean", "Garchomp": "Rough Skin", "Torkoal": "Drought"}
