"""Real Champions worker: typed support and a hidden planted investment profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import (
    HPReading,
    InferenceOptions,
    ProjectionRequest,
)
from src.domain.exceptions import CalcEngineError
from src.domain.models import CalcRequest, PokemonSet, StatSpread
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import prepare_series


@pytest.fixture(scope="module")
def engine():
    adapter = SmogonCandidateProjectionAdapter(ipc=NodeIpcClient(
        server_script=Path(__file__).resolve().parents[1] / "node_calc" / "calc_server.js", timeout_seconds=15,
    ))
    try:
        adapter.rules()
    except CalcEngineError as exc:
        adapter.close()
        pytest.skip(f"Champions Node worker unavailable: {exc}")
    yield adapter
    adapter.close()


def test_typed_projection_rejects_unknown_ability_without_false_zero_damage(engine):
    request = ProjectionRequest(calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp", ability="Invented"),
                                                 defender=PokemonSet(species="Incineroar"), move="Dragon Claw"),
                                before=HPReading(numerator=100, denominator=100, mode="percent"),
                                attacker_before=HPReading(numerator=100, denominator=100, mode="percent"))
    result = engine.project_batch([request])[0]
    assert not result.supported and result.reason


def test_real_engine_search_retains_a_hidden_legal_profile(engine):
    attacker = PokemonSet(species="Garchomp", ability="Rough Skin", item="", nature="Adamant",
                          evs=StatSpread(hp=16, atk=256, spe=256))
    defender = PokemonSet(species="Incineroar", ability="Intimidate", item="", nature="Impish",
                          evs=StatSpread(hp=256, def_=256, spd=16))
    planted = ProjectionRequest(calc=CalcRequest(gen=0, attacker=attacker, defender=defender, move="Dragon Claw"),
                               attacker_points={"hp": 2, "atk": 32, "spe": 32}, attacker_nature="Adamant",
                               defender_points={"hp": 32, "def": 32, "spd": 2}, defender_nature="Impish",
                               before=HPReading(numerator=202, denominator=202, mode="exact"),
                               attacker_before=HPReading(numerator=185, denominator=185, mode="exact"))
    damage = engine.project_batch([planted])[0].witnesses[0].damage
    source = "\n".join([
        "|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)",
        "|player|p1|Alice|", "|player|p2|Bob|", "|poke|p1|Garchomp, L50|", "|poke|p2|Incineroar, L50|",
        "|switch|p1a: Garchomp|Garchomp, L50|185/185", "|switch|p2a: Incineroar|Incineroar, L50|202/202",
        "|turn|1", "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        f"|-damage|p2a: Incineroar|{202-damage}/202", "|win|Alice",
    ])
    sheet = {"registered_team": True, "pokemon": [
        {"participant": "Alice", "slot": 1, "species": "Garchomp", "ability": "Rough Skin", "item": "",
         "nature": "Adamant", "stat_points": {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}},
        {"participant": "Bob", "slot": 1, "species": "Incineroar", "ability": "Intimidate", "item": ""},
    ]}
    request = Bo3AnalysisRequest(session_id="real", games=[source], teamsheet=sheet,
                                 inference_options=InferenceOptions(hp_mode="exact", timeout_seconds=60))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=engine).analyze(request, evidence, states)
    checkpoint = result.checkpoints[-1]
    assert checkpoint.coverage == "exhaustive"
    assert checkpoint.outcome == "ambiguous"
    target = next(summary for summary in checkpoint.summaries if summary.pokemon_id == "Bob:slot1")
    assert target.stat_points["hp"].minimum <= 32 <= target.stat_points["hp"].maximum
    assert target.stat_points["def"].minimum <= 32 <= target.stat_points["def"].maximum
    assert "Impish" in target.natures
    assert checkpoint.compatible_families > 0


def test_reported_public_bo3_retrieves_natures_and_narrows_investment_without_model_calls(engine):
    from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
    from tests.test_public_teamsheets import public_games

    request = Bo3AnalysisRequest(session_id="reported-public-series", games=public_games(),
                                 inference_options=InferenceOptions(timeout_seconds=60))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=engine,
                                         public_sheets=ShowdownTeamSheetParser())
    result = coordinator.analyze(request, evidence, states)
    assert result.continuity == "registered_team"
    final = result.checkpoints[-1]
    assert final.coverage == "bounded" and final.outcome == "ambiguous"
    assert len(final.observation_ids) >= 18
    assert len(final.local_constraints) >= 1
    assert all(summary.nature_declared and len(summary.natures) == 1 for summary in final.summaries)
    narrowed = next(summary for summary in final.summaries if summary.species == "Incineroar")
    assert narrowed.stat_points["hp"].minimum > 0
    assert narrowed.basis == "necessary_local_bounds"
    assert not any("Protect" in reason or "Grassy Surge" in reason for reason in final.unsupported)
    assert result.projections <= request.inference_options.max_projections


def test_sauron_real_series_restores_eleven_damage_samples_with_the_actual_worker(engine):
    from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
    from src.services.bo3_inference.declarations import identities, visible_declarations
    from src.services.bo3_inference.observations import observations
    from tests.test_public_teamsheets import sauron_games

    request = Bo3AnalysisRequest(session_id="sauron-damage-samples", games=sauron_games())
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    sheet = ShowdownTeamSheetParser().parse(request.games)
    initial, mappings = identities(evidence, states, sheet)
    counts = []
    for number, (state, mapping) in enumerate(zip(states, mappings), 1):
        declared = visible_declarations(initial, sheet, number, "public")
        found, skipped = observations(state, number, declared, mapping, engine, "percent")
        counts.append(len(found))
        assert not any("Unmodeled turn effect" in reason or "Unmodeled volatile effect" in reason for reason in skipped)
        assert all(declared[key].nature for sample in found for key in (sample.attacker_id, sample.defender_id))
    assert counts == [4, 5, 2]
