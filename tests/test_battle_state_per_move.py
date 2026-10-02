"""Regression tests: every per-move re-check uses the battle state AT THAT MOVE.

Reported problems this file pins down:

- weather was one value for the whole game (the LAST one), so a weather war
  (Drought sun replaced by Drizzle rain) mis-calculated every earlier turn;
- a status applied on turn N changed the calcs of turns BEFORE N, and a
  cured status was never removed;
- terrain, Reflect/Light Screen/Aurora Veil, Helping Hand and Friend Guard
  never reached the calc;
- items revealed or consumed during the game were ignored (always Chaos's
  most common item), and KO chances assumed a full-HP target;
- a mirror match (same species on both sides) collapsed into one Pokemon.

Every log goes through the real parser; the calc is a recording fake, so the
assertions are about exactly what reached the engine.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import (
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    MoveInfo,
    PokemonMetaSummary,
    SpeedComparison,
)
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import fake_move_info


class _RecordingCalc:
    def __init__(self) -> None:
        self.requests: list[CalcRequest] = []

    def calculate(self, request: CalcRequest) -> DamageResult:
        self.requests.append(request)
        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, damage_rolls=[10, 12], min_percent=10.0, max_percent=12.0,
            ko_chance_text="guaranteed 9HKO", description="",
        )

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        return SpeedComparison(
            faster=request.attacker.species, slower=request.defender.species,
            faster_speed=100, slower_speed=50,
        )

    def move_info(self, gen: int, move: str) -> MoveInfo:
        return fake_move_info(move)

    def forme_resolves(self, gen: int, species: str) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover
        pass

    def damage_requests(self, attacker: str, move: str, defender: str) -> list[CalcRequest]:
        return [
            r for r in self.requests
            if (r.attacker.species, r.move, r.defender.species) == (attacker, move, defender)
        ]


def _parse(*lines: str) -> GameState:
    header = ["|player|p1|Ash|", "|player|p2|Gary|", "|start"]
    return ShowdownReplayParser().parse("\n".join(header + list(lines)))


def _run(state: GameState, meta: MetaContext | None = None) -> _RecordingCalc:
    calc = _RecordingCalc()
    TurnReplaySimulator(calc).simulate(state, meta or MetaContext())
    return calc


def test_weather_war_applies_each_weather_only_where_it_was_up():
    state = _parse(
        "|switch|p1a: Torkoal|Torkoal, L50|100/100",
        "|switch|p1b: Venusaur|Venusaur, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|-weather|SunnyDay|[from] ability: Drought|[of] p1a: Torkoal",
        "|turn|1",
        "|move|p1a: Torkoal|Heat Wave|p2a: Garchomp|[spread] p2a",
        "|-damage|p2a: Garchomp|60/100",
        "|switch|p2a: Politoed|Politoed, L50|100/100",
        "|-weather|RainDance|[from] ability: Drizzle|[of] p2a: Politoed",
        "|move|p1b: Venusaur|Earth Power|p2a: Politoed",
        "|-damage|p2a: Politoed|70/100",
        "|-weather|RainDance|[upkeep]",
        "|turn|2",
        "|move|p1a: Torkoal|Eruption|p2a: Politoed|[spread] p2a",
        "|-damage|p2a: Politoed|50/100",
    )
    calc = _run(state)
    assert calc.damage_requests("Torkoal", "Heat Wave", "Garchomp")[0].field.weather == "Sun"
    assert calc.damage_requests("Venusaur", "Earth Power", "Politoed")[0].field.weather == "Rain"
    assert calc.damage_requests("Torkoal", "Eruption", "Politoed")[0].field.weather == "Rain"
    assert state.field is not None
    assert state.field.weather_on(1) == ["Sun", "Rain"]  # both were up during turn 1
    weather_events = [e.text for e in state.outcome.events if e.kind == "weather"]
    assert weather_events == [
        "Weather became Sun (from p1 Torkoal's Drought).",
        "Weather became Rain (from p2 Politoed's Drizzle).",
    ]


def test_status_only_applies_from_when_it_happened_and_is_removed_on_cure():
    state = _parse(
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Amoonguss|Amoonguss, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Earthquake|p2a: Amoonguss|[spread] p2a",
        "|-damage|p2a: Amoonguss|80/100",
        "|move|p2a: Amoonguss|Will-O-Wisp|p1a: Garchomp",
        "|-status|p1a: Garchomp|brn",
        "|turn|2",
        "|move|p1a: Garchomp|Earthquake|p2a: Amoonguss|[spread] p2a",
        "|-damage|p2a: Amoonguss|70/100",
        "|-enditem|p1a: Garchomp|Lum Berry|[eat]",
        "|-curestatus|p1a: Garchomp|brn|[msg]",
        "|turn|3",
        "|move|p1a: Garchomp|Earthquake|p2a: Amoonguss|[spread] p2a",
        "|-damage|p2a: Amoonguss|60/100",
    )
    calc = _run(state)
    # Garchomp's Earthquake is also re-checked as a THREAT while Amoonguss
    # moves, so pick each of Garchomp's own turns by Amoonguss's HP then.
    requests = calc.damage_requests("Garchomp", "Earthquake", "Amoonguss")
    by_hp = {r.defender_hp_percent: r for r in requests}
    turn1, turn3 = by_hp[100.0], by_hp[70.0]
    turn2 = next(r for r in requests if r.attacker.status == "brn")
    assert turn2.defender_hp_percent == 80.0
    assert all(r.attacker.status is None for r in requests if r.defender_hp_percent == 100.0)
    assert turn1.attacker.status is None  # the burn came AFTER this move
    assert turn3.attacker.status is None  # cured by Lum Berry
    # The Lum Berry was its original item (revealed on turn 2), held until eaten.
    assert turn1.attacker.item == "Lum Berry"
    assert turn3.attacker.item is None
    kinds = [e.kind for e in state.outcome.events]
    assert "status" in kinds and "cure" in kinds and "item" in kinds


def test_item_revealed_later_applies_from_the_start_and_trick_swaps_it():
    state = _parse(
        "|switch|p1a: Gholdengo|Gholdengo, L50|100/100",
        "|switch|p2a: Indeedee|Indeedee-F, L50|100/100",
        "|turn|1",
        "|move|p1a: Gholdengo|Make It Rain|p2a: Indeedee|[spread] p2a",
        "|-damage|p2a: Indeedee|50/100",
        "|turn|2",
        "|-item|p1a: Gholdengo|Choice Scarf|[from] ability: Frisk|[of] p2a: Indeedee",
        "|move|p2a: Indeedee|Trick|p1a: Gholdengo",
        "|-item|p1a: Gholdengo|Lagging Tail|[from] move: Trick",
        "|-item|p2a: Indeedee|Choice Scarf|[from] move: Trick",
        "|turn|3",
        "|move|p1a: Gholdengo|Shadow Ball|p2a: Indeedee",
        "|-damage|p2a: Indeedee|10/100",
    )
    calc = _run(state)
    turn1 = calc.damage_requests("Gholdengo", "Make It Rain", "Indeedee-F")[0]
    turn3 = calc.damage_requests("Gholdengo", "Shadow Ball", "Indeedee-F")[-1]  # [0] is a turn-1 alternative
    assert turn1.attacker.item == "Choice Scarf"  # revealed on turn 2, held since turn 1
    assert turn3.attacker.item == "Lagging Tail"  # after the Trick
    assert turn3.defender.item == "Choice Scarf"
    gholdengo = next(m for s in state.sides for m in s.team if m.species == "Gholdengo")
    assert gholdengo.item == "Choice Scarf"  # the ORIGINAL item, not the tricked one


def test_current_hp_reaches_the_calc_and_the_turn_check():
    state = _parse(
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Amoonguss|Amoonguss, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Amoonguss",
        "|-damage|p2a: Amoonguss|55/100",
        "|-heal|p2a: Amoonguss|61/100|[from] item: Leftovers",
        "|turn|2",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Amoonguss",
        "|-damage|p2a: Amoonguss|20/100",
    )
    calc = _run(state)
    turn1, turn2 = calc.damage_requests("Garchomp", "Dragon Claw", "Amoonguss")[:2]
    assert turn1.defender_hp_percent == 100.0
    assert turn2.defender_hp_percent == 61.0  # after Leftovers, not full HP
    assert turn2.defender.item == "Leftovers"
    checks = TurnReplaySimulator(_RecordingCalc()).simulate(state, MetaContext())
    assert checks[1].damage_checks[0].target_hp_before_percent == 61.0
    assert checks[1].damage_checks[0].target_player == "p2"


def test_terrain_screens_helping_hand_and_friend_guard_reach_the_calc():
    state = _parse(
        "|switch|p1a: Pincurchin|Pincurchin, L50|100/100",
        "|switch|p1b: Amoonguss|Amoonguss, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2b: Clefairy|Clefairy, L50|100/100",
        "|-fieldstart|move: Electric Terrain|[from] ability: Electric Surge|[of] p1a: Pincurchin",
        "|turn|1",
        "|move|p2b: Clefairy|Reflect|p2b: Clefairy",
        "|-sidestart|p2: Gary|Reflect",
        "|move|p1b: Amoonguss|Helping Hand|p1a: Pincurchin",
        "|-singleturn|p1a: Pincurchin|move: Helping Hand|[of] p1b: Amoonguss",
        "|move|p1a: Pincurchin|Rising Voltage|p2a: Garchomp",
        "|-damage|p2a: Garchomp|90/100",
        "|turn|2",
        "|move|p1a: Pincurchin|Rising Voltage|p2a: Garchomp",
        "|-damage|p2a: Garchomp|80/100",
    )
    meta = MetaContext(
        pokemon_stats={"Clefairy": PokemonMetaSummary(top_abilities={"Friend Guard": 95.0})}
    )
    calc = _run(state, meta)
    helped, unhelped = calc.damage_requests("Pincurchin", "Rising Voltage", "Garchomp")[:2]
    assert helped.field.terrain == "Electric"
    assert helped.field.defender_side.reflect is True
    assert helped.field.defender_side.friend_guard is True
    assert helped.field.attacker_side.helping_hand is True
    assert unhelped.field.attacker_side.helping_hand is False  # Helping Hand is one turn only
    move_texts = [e.text for e in state.outcome.events if e.kind == "move"]
    assert any("Rising Voltage (helping hand)" in t for t in move_texts)


def test_mirror_match_keeps_each_sides_pokemon_distinct():
    state = _parse(
        "|switch|p1a: Incineroar|Incineroar, L50|100/100",
        "|switch|p2a: Incineroar|Incineroar, L50|100/100",
        "|turn|1",
        "|move|p1a: Incineroar|Flare Blitz|p2a: Incineroar",
        "|-damage|p2a: Incineroar|60/100",
        "|move|p2a: Incineroar|Knock Off|p1a: Incineroar",
        "|-damage|p1a: Incineroar|75/100",
    )
    checks = TurnReplaySimulator(_RecordingCalc()).simulate(state, MetaContext())
    p1_check, p2_check = checks
    assert {a.move for a in p1_check.best_alternatives} == {"Flare Blitz"}
    assert {a.move for a in p2_check.best_alternatives} == {"Knock Off"}
    assert p1_check.damage_checks[0].target_player == "p2"
    assert p1_check.damage_checks[0].actual_hp_remaining_percent == 60.0
    assert p2_check.damage_checks[0].target_player == "p1"
    assert p2_check.damage_checks[0].actual_hp_remaining_percent == 75.0
