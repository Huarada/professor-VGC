"""Structured factual selection, causal provenance and bounded safe narration."""

import json

import pytest
from pydantic import ValidationError

from src.domain.bo3_inference.models import STATS, AttributeEnvelope, PointRange
from src.domain.narrative_claims import NarrativeClaimPlan
from src.services.bo3_claim_plan import build_claim_plan
from src.services.bo3_narrative_validation import validate_draft
from src.services.ground_truth import explain_champions_only
from tests.test_spread_interpretation import narrowed


def checkpoint():
    current = narrowed()
    mon = current.summaries[0].model_copy(update={
        "nature_declared": True, "declared_ability": "Cursed Body", "declared_item": "Gengarite",
        "declared_moves": ("Sludge Bomb", "Protect"),
        "attributes": (AttributeEnvelope(species="Gengar-Mega", supported=True, forme_ability="Shadow Tag",
            attributes={stat: PointRange(minimum=100, maximum=132) for stat in STATS}),),
    })
    return current.model_copy(update={"summaries": (mon,)})


def draft(plan, analysis="Protect preserved positioning while the opponent applied pressure.", ids=None):
    return json.dumps({"claim_ids": list(plan.required_claim_ids if ids is None else ids), "analysis": analysis})


def test_plan_has_real_checkpoint_references_and_does_not_turn_lookup_hits_into_proof():
    source = checkpoint()
    original = source.model_dump_json()
    plan = build_claim_plan(source, "What are the EV spreads?")
    bound = next(claim for claim in plan.claims if claim.kind == "bounds")
    assert bound.value == (12, 24) and bound.stat == "hp"
    assert bound.source.checkpoint_id == source.id
    assert bound.source.pokemon_id == "Alice:slot1" and bound.source.field == "summary.stat_points.hp"
    assert "hit1" not in bound.model_dump_json()
    assert source.model_dump_json() == original
    assert next(claim for claim in plan.claims if claim.kind == "forme_ability").value == "Shadow Tag"


def test_valid_draft_uses_exact_rendered_values_without_a_second_call():
    plan = build_claim_plan(checkpoint(), "What are the EV spreads?")
    calls = []

    def explain(correction):
        calls.append(correction)
        return draft(plan)

    text = explain_champions_only(explain, bo3=True, plan=plan)
    assert len(calls) == 1 and "HP compatible native points: 12-24" in text
    assert "declared nature: Modest" in text and "declared/verified moves: Sludge Bomb, Protect" in text
    assert "Gengar-Mega ability: Shadow Tag" in text
    assert "complete spread remains unresolved" in text
    assert "Strategic interpretation" in text and "did not pass" not in text


@pytest.mark.parametrize("analysis", [
    "Gengar-Mega benefits from Cursed Body.",
    "Ability: Cursed Body. Nature: Timid.",
    "Common Moves: Shadow Ball and Protect.",
    "The standard set is typically used here.",
    "There is no information about the stats.",
    "The winning decision was Protect.",
    "This caused the forfeit.",
    "The original spread is 32 HP / 32 SpA.",
])
def test_invalid_facts_are_retried_once_then_replaced_with_verified_findings(analysis):
    plan = build_claim_plan(checkpoint(), "EV spread and winning decision?")
    calls = []

    def explain(correction):
        calls.append(correction)
        return draft(plan, analysis)

    text = explain_champions_only(explain, bo3=True, plan=plan)
    assert len(calls) == 2 and calls[1]
    assert "did not pass evidence validation" in text and analysis not in text
    assert "12-24" in text and "Shadow Tag" in text


def test_missing_bounds_unknown_ids_duplicate_ids_and_extra_values_are_rejected():
    plan = build_claim_plan(checkpoint(), "spread")
    for ids in ((), ("another-player-claim",), (*plan.required_claim_ids, plan.required_claim_ids[0])):
        _, issues = validate_draft(draft(plan, ids=ids), plan)
        assert issues
    payload = json.loads(draft(plan))
    payload["fabricated_spread"] = {"hp": 32}
    assert validate_draft(json.dumps(payload), plan)[1]


def test_correction_can_recover_and_a_winning_question_requires_an_answer():
    plan = build_claim_plan(checkpoint(), "What was the winning decision?")
    assert validate_draft(draft(plan, ""), plan)[1]
    calls = []

    def explain(correction):
        calls.append(correction)
        return draft(plan, ids=()) if not correction else draft(plan)

    text = explain_champions_only(explain, bo3=True, plan=plan)
    assert len(calls) == 2 and "Strategic interpretation" in text and "did not pass" not in text


def test_plan_rejects_future_other_player_and_wrong_attribute_provenance():
    original = build_claim_plan(checkpoint(), "spread").model_dump(mode="json")
    for field, value in (("game", 3), ("pokemon_id", "Bob:slot1"), ("checkpoint_id", "g2:after_game")):
        payload = json.loads(json.dumps(original))
        payload["claims"][0]["source"][field] = value
        with pytest.raises(ValidationError):
            NarrativeClaimPlan.model_validate(payload)
    payload = json.loads(json.dumps(original))
    bound = next(claim for claim in payload["claims"] if claim["kind"] == "bounds")
    bound["source"]["field"] = "summary.stat_points.spe"
    with pytest.raises(ValidationError):
        NarrativeClaimPlan.model_validate(payload)


def test_conflict_does_not_credit_retained_bounds_and_plain_bo1_is_unchanged():
    source = checkpoint().model_copy(update={"outcome": "conflict"})
    plan = build_claim_plan(source, "spread")
    assert not any(claim.kind == "bounds" for claim in plan.claims)
    assert explain_champions_only(lambda correction: "Ordinary BO1 review.") == "Ordinary BO1 review."


def test_fallback_logs_validation_reasons_without_logging_generated_text(caplog):
    plan = build_claim_plan(checkpoint(), "spread")
    text = "Ability: Cursed Body. Private generated prose sentinel."
    explain_champions_only(lambda correction: draft(plan, text), bo3=True, plan=plan)
    assert "attempt 1 rejected" in caplog.text and "attempt 2 rejected" in caplog.text
    assert "Do not restate trait lists" in caplog.text
    assert "Private generated prose sentinel" not in caplog.text
