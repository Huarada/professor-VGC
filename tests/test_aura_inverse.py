"""Passive Mega ally aura, event-time removal, and reverse native damage search."""

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import (
    HPReading,
    InferenceOptions,
    ProjectionRequest,
)
from src.domain.models import CalcField, CalcRequest, PokemonSet
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import observations
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference_engine import (
    engine,  # noqa: F401 -- shared native worker fixture
)


def test_native_passive_floette_mega_aura_narrows_spd_and_stops_after_switch(engine):  # noqa: F811
    planted = ProjectionRequest(calc=CalcRequest(gen=0,
        attacker=PokemonSet(species="Primarina", nature="Modest", ability="Torrent", item=""),
        defender=PokemonSet(species="Incineroar", nature="Calm", ability="Intimidate", item=""),
        move="Moonblast", field=CalcField(fairy_aura=True)),
        attacker_points={"spa": 32}, attacker_nature="Modest",
        defender_points={"hp": 32, "spd": 32}, defender_nature="Calm",
        before=HPReading(numerator=202, denominator=202, mode="exact"),
        attacker_before=HPReading(numerator=155, denominator=155, mode="exact"))
    projected = engine.project_batch([planted])[0]
    assert projected.supported and projected.witnesses
    damage = projected.witnesses[0].damage
    remaining = 202 - damage
    plain = planted.model_copy(update={"calc": planted.calc.model_copy(update={"field": CalcField()}),
        "before": HPReading(numerator=remaining, denominator=202, mode="exact")})
    second = engine.project_batch([plain])[0].witnesses[0].damage
    source = "\n".join([
        "|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)", "|player|p1|Alice|", "|player|p2|Bob|",
        "|poke|p1|Primarina, L50|", "|poke|p1|Floette-Eternal, L50|", "|poke|p1|Garchomp, L50|",
        "|poke|p2|Incineroar, L50|", "|switch|p1a: Primarina|Primarina, L50|155/155",
        "|switch|p1b: Fl|Floette-Eternal, L50|149/149", "|switch|p2a: Incineroar|Incineroar, L50|202/202",
        "|turn|1", "|detailschange|p1b: Fl|Floette-Mega, L50",
        "|move|p1a: Primarina|Moonblast|p2a: Incineroar", f"|-damage|p2a: Incineroar|{remaining}/202",
        "|turn|2", "|switch|p1b: Garchomp|Garchomp, L50|183/183",
        "|move|p1a: Primarina|Moonblast|p2a: Incineroar",
        f"|-damage|p2a: Incineroar|{max(0, remaining-second)}/202", "|win|Alice",
    ])
    zeros = dict.fromkeys(["hp", "atk", "def", "spa", "spd", "spe"], 0)
    sheet = {"registered_team": True, "pokemon": [
        {"participant": "Alice", "slot": 1, "species": "Primarina", "ability": "Torrent", "item": "",
         "nature": "Modest", "stat_points": {**zeros, "spa": 32}},
        {"participant": "Alice", "slot": 2, "species": "Floette-Eternal", "ability": "Flower Veil",
         "item": "Floettite", "nature": "Modest"},
        {"participant": "Alice", "slot": 3, "species": "Garchomp", "ability": "Rough Skin", "item": "", "nature": "Hardy"},
        {"participant": "Bob", "slot": 1, "species": "Incineroar", "ability": "Intimidate", "item": "",
         "nature": "Calm", "stat_points": {k: (32 if k == "hp" else 0) for k in zeros if k != "spd"}},
    ]}
    request = Bo3AnalysisRequest(session_id="aura", games=[source], teamsheet=sheet,
        inference_options=InferenceOptions(hp_mode="exact"))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    parsed = JsonTeamSheetParser().parse(sheet)
    initial, mappings = identities(evidence, states, parsed)
    declared = visible_declarations(initial, parsed, 1, "public")
    found, skipped = observations(states[0], 1, declared, mappings[0], engine, "exact")
    assert len(found) == 2, skipped
    assert [sample.request.field.fairy_aura for sample in found] == [True, False]
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=engine, attributes=engine).analyze(request, evidence, states)
    final = next(s for s in result.checkpoints[-1].summaries if s.pokemon_id == "Bob:slot1")
    assert final.stat_points["spd"].minimum > 0
    assert final.stat_points["spd"].minimum <= 32 <= final.stat_points["spd"].maximum
    assert final.baseline_stat_points["spd"].minimum == 0
    assert final.attributes[0].baseline_attributes
