"""Allocation prose cannot replace the deterministic Champions inference tables."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.bo3_inference.narration import (
    UNVALIDATED_INVESTMENT_NOTICE,
    coaching_context,
    contains_numerical_investment,
)
from src.domain.models import AnalysisResult, MetaContext, SelectionPlan
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import Bo3AnalysisService
from tests.conftest import PROMPTS, FakeLLM
from tests.test_bo3_analysis import game, request
from tests.test_bo3_inference import AnalyticProjection


@pytest.mark.parametrize("text", [
    "Baseline favors max offensive speed investment (32 SpA / 32 Spe).",
    "Standard defensive build: 32 HP.",
    "Investment corresponds to standard 0–252 EV scales.",
    "SpA: 32; Spe: 32", "**32 Atk** / **32 Spe**", "Stat-point spread: 32/32/2.",
])
def test_numerical_investment_is_reserved_for_typed_tables(text):
    assert contains_numerical_investment(text)


@pytest.mark.parametrize("text", [
    "IVs are fixed at 31; stat points range from 0 to 32.",
    "Shadow Ball dealt 32% damage, leaving 12% HP.",
    "The hit removed 32 HP. The series score is 1/2.",
    "Modest is declared. Compatible investments remain ambiguous.",
])
def test_observed_hp_and_general_policy_are_valid_coaching(text):
    assert not contains_numerical_investment(text)


def service(llm):
    class Pipeline:
        def analyze(self, req):
            return AnalysisResult(session_id=req.session_id, question=req.question, answer="Game review.",
                                  selection=SelectionPlan(), meta_context=MetaContext())

    return Bo3AnalysisService(
        parser=ShowdownReplayParser(), pipeline_factory=Pipeline, llm=llm, prompts=PROMPTS,
        inference=Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection()),
    )


def test_invented_baseline_is_retried_once_with_a_concrete_correction():
    class CorrectingLLM(FakeLLM):
        def complete(self, **kwargs):
            answer = super().complete(**kwargs)
            return answer if len(self.calls) == 1 else "The nature is declared; investment remains uncertain."

    llm = CorrectingLLM("{}", "Standard build: 32 SpA / 32 Spe.")
    result = service(llm).analyze(request(game()))
    assert len(llm.calls) == 2
    assert "Remove numerical investment" in llm.calls[-1]["messages"][-1].content
    assert "32 SpA" not in result.answer


def test_repeated_allocation_prose_is_omitted_without_aborting_the_series():
    llm = FakeLLM("{}", "Baseline build: 32 HP / 32 SpD.")
    result = service(llm).analyze(request(game()))
    assert result.answer == UNVALIDATED_INVESTMENT_NOTICE
    assert result.inference is not None and result.games
    assert not contains_numerical_investment(result.answer)
    assert len(llm.calls) == 2


@pytest.mark.parametrize("text", [
    "Ataque: 252; Defesa: 4; Velocidade: 252.",
    "**Ataque Especial** | **252**", "120 EVs em ataque.", "EVs: 120",
    "128 EVs in HP.", "Defesa Especial = 252", "252EVs",
])
def test_legacy_units_and_portuguese_allocation_formats_are_rejected(text):
    assert contains_numerical_investment(text)


def test_summary_rejects_allocations_even_when_inference_is_disabled():
    req = request(game()).model_copy(update={"inference_options":
        InferenceOptions(enabled=False)})
    llm = FakeLLM("{}", "Suggested investment: 252 SpA / 252 Spe.")
    result = service(llm).analyze(req)
    assert result.answer == UNVALIDATED_INVESTMENT_NOTICE
    assert result.inference is None
    assert len(llm.calls) == 2


@pytest.mark.parametrize("bo3", [True, False])
def test_shared_per_game_stage_checks_bo3_allocations_without_changing_bo1(sample_chaos_path, bo3):
    from src.domain.models import AnalysisRequest
    from src.services.ground_truth import explain_champions_only
    from tests.conftest import build_evidence

    assembler = build_evidence(sample_chaos_path)
    req = AnalysisRequest(session_id="units", replay_raw_text=game(),
                          series_context={"game": 1} if bo3 else None)
    state = assembler.prepare(ShowdownReplayParser(), req)
    evidence = assembler.assemble(request=req, game_state=state, selection=SelectionPlan(), history=[])
    calls = []

    def explain(correction):
        calls.append(correction)
        return "Recommended EVs: 252 SpA / 252 Spe."

    if bo3:
        answer, _ = assembler.explain_within_regulation(explain, evidence, state)
        assert answer == UNVALIDATED_INVESTMENT_NOTICE
        assert len(calls) == 2
        assert "at most 32 per stat and 66 total" in calls[-1]
    else:
        answer, _ = assembler.explain_within_regulation(explain, evidence, state)
        assert "252 SpA" in answer and len(calls) == 1
    # Mechanics and allocation failures share one retry budget rather than nesting loops.
    calls.clear()
    assert explain_champions_only(explain, bo3=True) == UNVALIDATED_INVESTMENT_NOTICE
    assert len(calls) == 2


@pytest.mark.parametrize("lower,upper", [(0, 252), (0, 33), (-1, 32), (20, 19)])
def test_typed_summary_cannot_expose_legacy_or_invalid_stat_point_bounds(lower, upper):
    from pydantic import ValidationError

    from src.domain.bo3_inference.models import CandidateSummary, PointRange

    with pytest.raises(ValidationError, match="0-32"):
        CandidateSummary(pokemon_id="Alice:slot1", species="Garchomp", natures=("Adamant",),
                         basis="declarations", stat_points={"atk": PointRange(minimum=lower, maximum=upper)})


def test_marginal_bounds_are_not_rejected_for_summing_above_the_joint_budget():
    from src.domain.bo3_inference.models import STATS, CandidateSummary, PointRange

    summary = CandidateSummary(pokemon_id="Alice:slot1", species="Garchomp", natures=("Adamant",),
        basis="declarations", stat_points={stat: PointRange(minimum=0, maximum=32) for stat in STATS})
    assert sum(bound.maximum for bound in summary.stat_points.values()) == 192



def test_bo3_narration_context_omits_investment_without_mutating_typed_evidence():
    from copy import deepcopy

    payload = {"question": "Can you infer 32 EVs?", "meta_context": {"top_spreads": ["252 SpA / 252 Spe"],
        "top_spread_evs": {"spa": 256}, "top_spread_nature": "Timid", "top_items": {"Life Orb": 10}},
        "set_inference": {"coverage": "bounded", "summaries": [{"natures": ["Modest"], "nature_declared": True,
            "stat_points": {"spa": {"minimum": 0, "maximum": 32}}, "declared_item": "Gengarite"}],
            "joint_examples": [{"points": {"spa": 32}}]},
        "damage": {"minimum_damage": 50, "description": "252 SpA Gengar: 50-60 damage", "hp_after": 40}}
    before = deepcopy(payload)
    projected = coaching_context(payload)
    assert payload == before
    assert projected["question"] == payload["question"]
    assert "top_spread_evs" not in projected["meta_context"]
    assert "top_spreads" not in projected["meta_context"]
    assert projected["set_inference"]["summaries"][0]["natures"] == ["Modest"]
    assert projected["set_inference"]["summaries"][0]["declared_item"] == "Gengarite"
    assert "stat_points" not in projected["set_inference"]["summaries"][0]
    assert projected["damage"]["minimum_damage"] == 50 and projected["damage"]["hp_after"] == 40
    assert "252" not in projected["damage"]["description"]


@pytest.mark.parametrize("bo3", [True, False])
def test_shared_context_projection_applies_only_to_bo3(sample_chaos_path, bo3):
    from src.domain.models import AnalysisRequest
    from src.services.ground_truth import build_explanation_context
    from tests.conftest import build_evidence

    assembler = build_evidence(sample_chaos_path)
    req = AnalysisRequest(session_id="context", replay_raw_text=game(), series_context={"game": 1} if bo3 else None)
    state = assembler.prepare(ShowdownReplayParser(), req)
    evidence = assembler.assemble(request=req, game_state=state, selection=SelectionPlan(), history=[])
    projected = build_explanation_context(evidence)
    for stat in projected["meta_context"]["pokemon_stats"].values():
        assert ("top_spreads" not in stat) == bo3
    assert all(hasattr(stat, "top_spreads") for stat in evidence.meta_context.pokemon_stats.values())


def test_summary_context_is_sanitized_but_returned_inference_retains_numeric_tables():
    import json

    llm = FakeLLM("{}", "The registered nature is known; compatible investment remains uncertain.")
    result = service(llm).analyze(request(game()))
    assert result.inference.checkpoints[-1].summaries[0].stat_points
    content = llm.calls[-1]["messages"][-1].content
    payload = json.loads(content[:content.rfind("}") + 1])
    for checkpoint in payload["set_inference"]["checkpoints"]:
        assert all("stat_points" not in summary for summary in checkpoint["summaries"])
        assert "joint_examples" not in checkpoint


def test_foreign_mechanics_still_fail_closed_after_the_single_correction():
    from src.domain.exceptions import UnsupportedRulesError
    from src.services.ground_truth import explain_champions_only

    calls = []
    def explain(correction):
        calls.append(correction)
        return "Use Terastallization with 252 SpA."
    with pytest.raises(UnsupportedRulesError):
        explain_champions_only(explain, bo3=True)
    assert len(calls) == 2



@pytest.mark.parametrize("text", [
    "The baseline hit removed 32 HP, leaving the target alive.",
    "The assumed spread's attack dealt 252 HP damage.",
    "Actual Speed: 172; this is the observed speed value.",
    "O ataque do baseline causou 32 HP de dano.",
])
def test_mechanical_quantities_are_not_false_positive_investment(text):
    assert not contains_numerical_investment(text)



def test_recovery_logs_the_reason_without_logging_the_unvalidated_model_text(caplog):
    from src.services.ground_truth import explain_champions_only

    unsafe = "private-marker 252 SpA / 252 Spe"
    assert explain_champions_only(lambda correction: unsafe, bo3=True) == UNVALIDATED_INVESTMENT_NOTICE
    assert "narrative omitted" in caplog.text
    assert "private-marker" not in caplog.text
