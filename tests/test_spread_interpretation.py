"""Computed stat findings reach coaching and remain distinct from original sets."""

import json

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import (
    AttributeEnvelope,
    InferenceOptions,
    PointRange,
)
from src.domain.bo3_inference.narration import coaching_context
from src.domain.models import AnalysisResult, MetaContext, SelectionPlan
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import Bo3AnalysisService
from src.ui.spread_summary import stat_spread_commentary
from tests.conftest import PROMPTS, FakeLLM
from tests.test_attribute_ranges import checkpoint
from tests.test_bo3_inference import AnalyticProjection, log, sheet


def narrowed():
    original = checkpoint()
    summary = original.summaries[0]
    points = {**summary.stat_points, "hp": PointRange(minimum=12, maximum=24)}
    return original.model_copy(update={"summaries": (summary.model_copy(update={
        "basis": "necessary_local_bounds", "stat_points": points}),),
        "local_constraints": ("hit1",), "observation_ids": ("hit1", "hit2")})


def test_validated_bounds_survive_coaching_projection_without_restoring_chaos_priors():
    before = narrowed()
    original = before.model_dump_json()
    payload = coaching_context({"trusted_stat_spread_findings": interpret_checkpoint(before).model_dump(mode="json"),
        "meta_context": {"top_spread_evs": {"hp": 256}}, "stat_points": {"hp": 256},
        "speed": {"faster_speed": 200}})
    findings = payload["trusted_stat_spread_findings"]["pokemon"][0]
    assert findings["constrained_native_bounds"]["hp"] == {"minimum": 12, "maximum": 24}
    assert findings["complete_spread_known"] is False and "spe" in findings["unresolved_axes"]
    assert "top_spread_evs" not in payload["meta_context"] and "stat_points" not in payload
    assert "faster_speed" not in payload["speed"]
    assert before.model_dump_json() == original


def test_declaration_only_attributes_do_not_become_identified_ev_spreads():
    original = checkpoint()
    summary = original.summaries[0]
    envelope = AttributeEnvelope(species="Gengar-Mega", supported=True, forme_ability="Shadow Tag",
        attributes={stat: PointRange(minimum=100, maximum=132) for stat in summary.stat_points})
    current = original.model_copy(update={"summaries": (summary.model_copy(update={"attributes": (envelope,)}),)})
    finding = interpret_checkpoint(current).pokemon[0]
    assert not finding.complete_spread_known and not finding.constrained_native_bounds
    assert finding.forms[0].meaning == "calculated_outer_ranges_not_observed_stats"
    text = stat_spread_commentary(current)
    assert "No original stat-point spread was identified" in text
    assert "Shadow Tag" in text and "base-form" in text
    assert "Numeric stats - level 50" in text
    for label in ("HP", "Atk", "Def", "SpA", "SpD", "Spe"):
        assert f"{label} **100-132**" in text
    assert "not Champions stat-point allocations" in text


def test_numeric_commentary_distinguishes_exact_values_and_ranges_without_guessing():
    original = checkpoint()
    summary = original.summaries[0]
    bounds = {stat: PointRange(minimum=100, maximum=132) for stat in summary.stat_points}
    bounds["spe"] = PointRange(minimum=150, maximum=150)
    base = AttributeEnvelope(species="Gengar", supported=True, attributes=bounds)
    mega = AttributeEnvelope(species="Gengar-Mega", supported=True, attributes={
        **bounds, "spe": PointRange(minimum=150, maximum=182)}, forme_ability="Shadow Tag")
    current = original.model_copy(update={"summaries": (summary.model_copy(update={"attributes": (base, mega)}),)})
    text = stat_spread_commentary(current)
    assert "**Gengar (Alice)**" in text and "**Gengar-Mega (Alice)**" in text
    assert "Spe **150**" in text and "Spe **150-182**" in text
    assert "Spe **182**" not in text
    assert "No original stat-point spread was identified" in text


def test_computed_text_uses_native_bounds_and_marks_incomplete_or_conflicting_evidence():
    text = stat_spread_commentary(narrowed())
    assert "HP 12-24" in text and "does not identify a complete spread" in text
    assert "not observed battle stats" in text
    conflict = narrowed().model_copy(update={"outcome": "conflict"})
    assert "retain earlier valid bounds" in stat_spread_commentary(conflict)


def test_native_and_attribute_reductions_reach_text_without_confidence_claims():
    original = narrowed()
    summary = original.summaries[0]
    baseline = {stat: PointRange(minimum=0, maximum=32) for stat in summary.stat_points}
    initial_attributes = {stat: PointRange(minimum=100, maximum=132) for stat in summary.stat_points}
    attributes = {**initial_attributes, "hp": PointRange(minimum=112, maximum=124)}
    envelope = AttributeEnvelope(species="Gengar", supported=True, attributes=attributes,
        baseline_attributes=initial_attributes)
    current = original.model_copy(update={"summaries": (summary.model_copy(update={
        "baseline_stat_points": baseline, "attributes": (envelope,)}),)})
    finding = interpret_checkpoint(current).pokemon[0]
    assert finding.range_reductions["hp"].reduction_percent == 60.6
    assert finding.forms[0].range_reductions["hp"].reduction_percent == 60.6
    text = stat_spread_commentary(current)
    assert "0-32 -> 12-24" in text and "60.6%" in text
    assert "HP **112-124** (initial 100-132" in text
    assert "not confidence or win probability" in text
    conflict = current.model_copy(update={"outcome": "conflict"})
    assert not interpret_checkpoint(conflict).pokemon[0].range_reductions
    assert "60.6%" not in stat_spread_commentary(conflict)


def test_budget_messages_are_not_counted_as_excluded_damage_observations():
    current = checkpoint().model_copy(update={"unsupported": (
        "Projection/time budget reached", "g1:e1:p1:Gengar: Critical hit",
        "g1:e1:p1:Gengar: Engine limitation", "g1:e2:p2:Incineroar: Unsupported move")})
    assert interpret_checkpoint(current).flagged_damage_observations == 2


def test_teamsheet_traits_remain_declared_facts_in_compact_coaching_findings():
    from src.services.bo3_prompt_context import compact_bo3_context

    original = checkpoint()
    current = original.model_copy(update={"summaries": (original.summaries[0].model_copy(update={
        "declared_item": "Gengarite", "declared_ability": "Cursed Body", "nature_declared": True,
        "declared_moves": ("Sludge Bomb", "Protect")}),)})
    findings = interpret_checkpoint(current).model_dump(mode="json")
    packed = compact_bo3_context({"trusted_stat_spread_findings": [findings]})
    pokemon = packed["trusted_stat_spread_findings"][0]["pokemon"][0]
    assert pokemon["nature_is_declared"] is True
    assert pokemon["declared_item"] == "Gengarite" and pokemon["declared_base_ability"] == "Cursed Body"
    assert pokemon["declared_moves"] == ["Sludge Bomb", "Protect"]


def test_per_game_and_series_models_receive_real_computed_spread_findings():
    seen = []

    class Pipeline:
        def analyze(self, request):
            seen.append(request.series_context)
            return AnalysisResult(session_id=request.session_id, question=request.question,
                answer="Defensive stats remain constrained within the model.",
                selection=SelectionPlan(), meta_context=MetaContext())

    llm = FakeLLM("{}", "Some defensive choices are constrained, while unresolved stats prevent a full spread claim.")
    req = Bo3AnalysisRequest(session_id="interpreted", games=[log(), log(winner="Bob", turn=2)],
        teamsheet=sheet(), inference_options=InferenceOptions(hp_mode="exact"))
    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
        llm=llm, prompts=PROMPTS, inference=Bo3InferenceCoordinator(
            sheets=JsonTeamSheetParser(), projection=AnalyticProjection())).analyze(req)
    for number, context in enumerate(seen, 1):
        assert all(entry["game"] == number for entry in context["trusted_stat_spread_findings"])
    payload = json.loads(llm.calls[0]["messages"][-1].content)
    entries = payload["trusted_stat_spread_findings"][-1]["pokemon"]
    target = next(entry for entry in entries if entry["species"] == "Incineroar")
    if "unchanged_from_checkpoint" in target:
        prior = next(checkpoint for checkpoint in payload["trusted_stat_spread_findings"]
                     if checkpoint["checkpoint_id"] == target["unchanged_from_checkpoint"])
        target = next(entry for entry in prior["pokemon"] if entry["pokemon_id"] == target["pokemon_id"])
    assert target["constrained_native_bounds"]["def"] == {"minimum": 20, "maximum": 20}
    assert result.inference is not None
