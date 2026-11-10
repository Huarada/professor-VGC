"""Public BO3 damage calibrated against separately supplied, held-out spreads."""

import json
from pathlib import Path

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.models import CandidateProfile, InferenceOptions
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import observations, reading
from src.services.bo3_inference.solver import projection_request
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference_engine import engine  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "series"


def public_series():
    public = json.loads((FIXTURES / "bo3-public-2709311277.json").read_text(encoding="utf-8"))
    request = Bo3AnalysisRequest(session_id="held-out-spreads", games=public["games"],
        inference_options=InferenceOptions(max_projections=8000))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    sheet = ShowdownTeamSheetParser().parse(request.games)
    initial, maps = identities(evidence, states, sheet)
    return request, evidence, states, sheet, initial, maps


def true_profiles(initial):
    # This file is used only for calibration and output scoring, never as a
    # teamsheet or inference input. Unspecified attributes were supplied as zero.
    truth = json.loads((FIXTURES / "bo3-truth-2709311277.json").read_text(encoding="utf-8"))["teams"]
    profiles = {}
    for key, entry in initial.items():
        known = truth[entry.participant][entry.species]
        assert sum(known["points"].values()) == 66 and all(0 <= value <= 32 for value in known["points"].values())
        profiles[key] = CandidateProfile(pokemon_id=key, species=entry.species,
            points=known["points"], natures=(known["nature"],))
    return profiles



def public_after_matches(hit, support):
    # Independent display oracle, including the protocol color discriminator.
    # Do not call production agrees()/displays() when scoring known profiles.
    for witness in support.witnesses:
        remaining = max(0, witness.hp_before - witness.damage)
        displayed = max(1, 100 * remaining // support.maximum_hp) if remaining else 0
        if displayed != hit.after.numerator:
            continue
        if hit.after.color and displayed == 20:
            if (remaining * 5 > support.maximum_hp) != (hit.after.color == "y"):
                continue
        if hit.after.color and displayed == 50:
            if (remaining * 2 > support.maximum_hp) != (hit.after.color == "g"):
                continue
        return True
    return False


def test_all_44_direct_hits_match_held_out_spreads_including_15_floette_hits(engine):  # noqa: F811
    _, _, states, sheet, initial, maps = public_series()
    hits = []
    counts = []
    for game, (state, mapping) in enumerate(zip(states, maps), 1):
        found, skipped = observations(state, game, visible_declarations(initial, sheet, game, "public"), mapping, engine, "percent")
        assert not skipped
        hits.extend(found)
        counts.append(len(found))
    assert counts == [10, 18, 16]
    assert sum(hit.attacker_id == "Huarada:slot3" or hit.defender_id == "Huarada:slot3" for hit in hits) == 15
    profiles = true_profiles(initial)
    projected = engine.project_batch([projection_request(hit, profiles) for hit in hits])
    for hit, support in zip(hits, projected):
        assert support.supported, (hit.id, support.reason)
        assert public_after_matches(hit, support), hit.id
    dragon = next((hit, support) for hit, support in zip(hits, projected) if hit.id == "g2:e13:p1:Garchomp")
    assert dragon[0].after.numerator == 31 and dragon[1].maximum_hp == 185
    assert {w.hp_before for w in dragon[1].witnesses} == {185}
    assert max(w.damage for w in dragon[1].witnesses) == 126


def test_blind_series_inference_retains_all_known_native_points_without_receiving_them(engine):  # noqa: F811
    request, evidence, states, sheet, initial, _ = public_series()
    assert request.teamsheet is None and all(not entry.stat_points for entry in sheet.pokemon)
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), public_sheets=ShowdownTeamSheetParser(),
        projection=engine).analyze(request, evidence, states)
    profiles = true_profiles(initial)
    assert [len(cp.observation_ids) for cp in result.checkpoints if cp.phase == "after_game"] == [10, 28, 44]
    for checkpoint in result.checkpoints:
        assert checkpoint.outcome != "conflict"
        for summary in checkpoint.summaries:
            for stat, bounds in summary.stat_points.items():
                assert bounds.minimum <= profiles[summary.pokemon_id].points[stat] <= bounds.maximum


def test_floette_offense_remains_27_to_32_even_conditioned_on_other_known_spreads(engine):  # noqa: F811
    # A conditional validation oracle, NOT the public inference workflow.
    _, _, states, sheet, initial, maps = public_series()
    hits = []
    for game, (state, mapping) in enumerate(zip(states, maps), 1):
        found, _ = observations(state, game, visible_declarations(initial, sheet, game, "public"), mapping, engine, "percent")
        hits.extend(hit for hit in found if hit.attacker_id == "Huarada:slot3")
    assert len(hits) == 11
    profiles = true_profiles(initial)
    compatible = []
    for spa in range(33):
        original = profiles["Huarada:slot3"]
        candidate = {**profiles, "Huarada:slot3": original.model_copy(update={"points": {**original.points, "spa": spa}})}
        predictions = engine.project_batch([projection_request(hit, candidate) for hit in hits])
        if all(result.supported and public_after_matches(hit, result) for hit, result in zip(hits, predictions)):
            compatible.append(spa)
    assert compatible == [27, 28, 29, 30, 31, 32]


def test_another_legal_floette_spread_also_matches_all_44_modeled_hits(engine):  # noqa: F811
    _, _, states, sheet, initial, maps = public_series()
    hits = []
    for game, (state, mapping) in enumerate(zip(states, maps), 1):
        found, _ = observations(state, game, visible_declarations(initial, sheet, game, "public"), mapping, engine, "percent")
        hits.extend(found)
    profiles = true_profiles(initial)
    original = profiles["Huarada:slot3"]
    alternate = {**original.points, "atk": 5, "spa": 27}
    assert sum(alternate.values()) == 66 and alternate != original.points
    profiles["Huarada:slot3"] = original.model_copy(update={"points": alternate})
    for hit, result in zip(hits, engine.project_batch([projection_request(hit, profiles) for hit in hits])):
        assert result.supported
        assert public_after_matches(hit, result), hit.id


def test_public_boundary_color_is_not_a_missing_hp_measurement():
    from src.domain.bo3_inference.constraints import displays

    yellow = reading("20/100y", "percent")
    red = reading("20/100r", "percent")
    green = reading("50/100g par", "percent")
    assert yellow and red and green
    assert displays(35, 170, yellow) and not displays(34, 170, yellow)
    assert displays(34, 170, red) and not displays(35, 170, red)
    assert displays(86, 170, green) and not displays(85, 170, green)
    assert reading("20/100g", "percent") is None
