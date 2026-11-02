"""Native planted reverse searches and parser attribution across damage classes."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import (
    DeclaredSet,
    HPReading,
    InferenceOptions,
    ProjectionRequest,
)
from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    BattleSnapshot,
    CalcRequest,
    GameState,
    MonState,
    PokemonSet,
    TargetHit,
)
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import field_for, observations
from src.services.bo3_inference.solver import relevant_axes
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference import AnalyticProjection
from tests.test_bo3_inference_engine import engine  # noqa: F401


@pytest.mark.parametrize("move,owner,axis,high_roll", [
    ("Body Press", "Alice", "def", True),
    ("Foul Play", "Bob", "atk", True),
    ("Psyshock", "Bob", "def", False),
    ("Eruption", "Alice", "spa", True),
    ("Dragon Claw", "Alice", "atk", True),
    ("Gyro Ball", "Bob", "spe", True),
    ("Shell Side Arm", "Bob", "def", False),
    ("Night Shade", "Bob", "hp", True),
])
def test_reverse_search_retains_planted_stats_for_each_damage_class(engine, move, owner, axis, high_roll):  # noqa: F811
    zeros = dict.fromkeys(["hp", "atk", "def", "spa", "spd", "spe"], 0)
    attack = {**zeros, "spa": 32} if move == "Psyshock" else dict(zeros)
    defense = {**zeros, "hp": 32}
    (attack if owner == "Alice" else defense)[axis] = 32
    critical = move == "Dragon Claw"
    before = 90 if move == "Eruption" else 183
    request = ProjectionRequest(calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp", ability="Pressure", item=""),
        defender=PokemonSet(species="Arcanine", ability="Intimidate", item=""), move=move, critical_hit=critical,
        spread_hit=False), attacker_points=attack, defender_points=defense,
        before=HPReading(numerator=197, denominator=197, mode="exact"),
        attacker_before=HPReading(numerator=before, denominator=183, mode="exact"))
    result = engine.project_batch([request])[0]
    assert result.supported and result.witnesses
    damage = (max if high_roll else min)(w.damage for w in result.witnesses)
    lines = ["|tier|[Gen 9 Champions] VGC 1976 Reg M-B (Bo3)",
        "|player|p1|Alice|", "|player|p2|Bob|", "|poke|p1|Garchomp, L50|", "|poke|p2|Arcanine, L50|",
        f"|switch|p1a: Garchomp|Garchomp, L50|{before}/183", "|switch|p2a: Arcanine|Arcanine, L50|197/197",
        "|turn|1", f"|move|p1a: Garchomp|{move}|p2a: Arcanine"]
    if critical:
        lines.append("|-crit|p2a: Arcanine")
    lines += [f"|-damage|p2a: Arcanine|{max(0,197-damage)}/197", "|win|Alice"]
    sheet = {"registered_team": True, "pokemon": [
        {"participant": "Alice", "slot": 1, "species": "Garchomp", "ability": "Pressure", "item": "", "nature": "Hardy",
         "stat_points": {k: v for k, v in attack.items() if (owner, axis) != ("Alice", k)}},
        {"participant": "Bob", "slot": 1, "species": "Arcanine", "ability": "Intimidate", "item": "", "nature": "Hardy",
         "stat_points": {k: v for k, v in defense.items() if (owner, axis) != ("Bob", k)}},
    ]}
    series = Bo3AnalysisRequest(session_id="damage-class", games=["\n".join(lines)], teamsheet=sheet,
        inference_options=InferenceOptions(hp_mode="exact"))
    evidence, states = prepare_series(ShowdownReplayParser(), series)
    parsed = JsonTeamSheetParser().parse(sheet)
    initial, mappings = identities(evidence, states, parsed)
    samples, skipped = observations(states[0], 1, visible_declarations(initial, parsed, 1, "public"), mappings[0], engine, "exact")
    assert len(samples) == 1, skipped
    assert axis in relevant_axes(samples)[owner + ":slot1"]
    inferred = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=engine).analyze(series, evidence, states)
    final = next(s for s in inferred.checkpoints[-1].summaries if s.pokemon_id == owner + ":slot1")
    assert inferred.checkpoints[-1].coverage == "exhaustive"
    assert 0 < final.stat_points[axis].minimum <= 32 <= final.stat_points[axis].maximum


def test_critical_marker_applies_only_to_its_side_qualified_spread_target():
    log = "\n".join(["|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)",
        "|player|p1|Alice|", "|player|p2|Bob|", "|switch|p1a: X|Garchomp, L50|183/183",
        "|switch|p1b: Y|Incineroar, L50|170/170", "|switch|p2a: Z|Incineroar, L50|170/170",
        "|turn|1", "|move|p1a: X|Earthquake|p2a: Z|[spread] p1b,p2a",
        "|-crit|p2a: Z", "|-damage|p1b: Y|150/170", "|-damage|p2a: Z|140/170", "|win|Alice"])
    game = ShowdownReplayParser().parse(log)
    event = next(e for e in game.outcome.events if e.kind == "move")
    assert {(h.player, h.species): h.critical_hit for h in event.hits} == {
        ("p1", "Incineroar"): False, ("p2", "Incineroar"): True}


def test_passive_weather_suppression_and_friend_guard_are_side_aware():
    event = BattleEvent(turn=1, kind="move", actor_player="p1", state=BattleSnapshot(weather="Sun"))
    active = {("p1", "Garchomp"): PokemonSet(species="Garchomp", ability="Pressure"),
        ("p2", "Golduck"): PokemonSet(species="Golduck", ability="Cloud Nine"),
        ("p2", "Maushold"): PokemonSet(species="Maushold", ability="Friend Guard")}
    field = field_for(event, "p2", "Golduck", active)
    assert field.weather == "" and field.defender_side.friend_guard
    assert not field_for(event, "p2", "Maushold", active).defender_side.friend_guard
    del active[("p2", "Golduck")]
    assert field_for(event, "p2", "Maushold", active).weather == "Sun"


@pytest.mark.parametrize("interleaved", [False, True])
def test_logged_multi_hit_count_requires_stable_damage_state(engine, interleaved):  # noqa: F811
    source = "\n".join(["|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)",
        "|player|p1|Alice|", "|player|p2|Bob|", "|poke|p1|Garchomp, L50|", "|poke|p2|Incineroar, L50|",
        "|switch|p1a: Garchomp|Garchomp, L50|183/183", "|switch|p2a: Incineroar|Incineroar, L50|170/170",
        "|turn|1", "|move|p1a: Garchomp|Triple Axel|p2a: Incineroar", "|-damage|p2a: Incineroar|165/170",
        *( ["|-heal|p2a: Incineroar|169/170|[from] item: Sitrus Berry"] if interleaved else []),
        "|-damage|p2a: Incineroar|155/170", "|-damage|p2a: Incineroar|140/170",
        "|-hitcount|p2a: Incineroar|3", "|win|Alice"])
    sheet = {"registered_team": True, "pokemon": [
        {"participant": "Alice", "slot": 1, "species": "Garchomp", "ability": "Pressure", "item": "", "nature": "Hardy"},
        {"participant": "Bob", "slot": 1, "species": "Incineroar", "ability": "Intimidate", "item": "", "nature": "Hardy"}]}
    request = Bo3AnalysisRequest(session_id="multi", games=[source], teamsheet=sheet)
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    parsed = JsonTeamSheetParser().parse(sheet)
    initial, mappings = identities(evidence, states, parsed)
    found, skipped = observations(states[0], 1, visible_declarations(initial, parsed, 1, "public"), mappings[0], engine, "exact")
    if interleaved:
        assert not found and any("interleaved" in reason for reason in skipped)
    else:
        assert len(found) == 1 and found[0].request.hits == 3
        assert found[0].before.numerator == 170 and found[0].after.numerator == 140
        # Parsed observations use summed native rolls, never one flattened hit.
        projected = engine.project_batch([ProjectionRequest(calc=found[0].request,
            before=found[0].before, attacker_before=found[0].attacker_before)])[0]
        assert projected.supported and projected.witnesses


@pytest.mark.parametrize("ability", ["Fairy Aura", "Cloud Nine", "Air Lock", "Friend Guard"])
def test_effect_holder_ko_during_spread_does_not_apply_one_global_state_to_every_target(ability):
    mapping = {("p1", "Garchomp"): "Alice:slot1", ("p2", "Golduck"): "Bob:slot1", ("p2", "Arcanine"): "Bob:slot2"}
    declarations = {key: DeclaredSet(participant=key.split(":")[0], slot=int(key[-1]), species=species,
        item="", ability=ability if species == "Golduck" else "Pressure")
        for (_, species), key in mapping.items()}
    state = BattleSnapshot(active={"p1": ["Garchomp"], "p2": ["Golduck", "Arcanine"]},
        mons={f"{side}:{species}": MonState(hp_raw="100/100") for side, species in mapping})
    event = BattleEvent(turn=1, kind="move", actor_player="p1", actor="Garchomp", move="Earthquake", state=state,
        hits=[TargetHit(player="p2", species="Golduck", hp_after=0, hp_before_raw="100/100", hp_after_raw="0 fnt"),
              TargetHit(player="p2", species="Arcanine", hp_after=50, hp_before_raw="100/100", hp_after_raw="50/100")])
    found, skipped = observations(GameState(outcome=BattleOutcome(events=[event])), 1,
        declarations, mapping, AnalyticProjection(), "percent")
    assert not found and len(skipped) == 2
    assert all("per-target effect timing" in reason for reason in skipped)
