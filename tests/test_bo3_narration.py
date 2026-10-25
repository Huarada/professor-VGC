"""Allocation prose cannot replace the deterministic Champions inference tables."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.bo3_inference.narration import contains_numerical_investment
from src.domain.exceptions import LLMProviderError
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


def test_repeated_allocation_prose_fails_explicitly_without_unbounded_model_calls():
    llm = FakeLLM("{}", "Baseline build: 32 HP / 32 SpD.")
    with pytest.raises(LLMProviderError, match="numerical investment"):
        service(llm).analyze(request(game()))
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
    with pytest.raises(LLMProviderError, match="numerical investment"):
        service(llm).analyze(req)
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
        with pytest.raises(LLMProviderError, match="numerical investment"):
            assembler.explain_within_regulation(explain, evidence, state)
        assert len(calls) == 2
        assert "at most 32 per stat and 66 total" in calls[-1]
    else:
        answer, _ = assembler.explain_within_regulation(explain, evidence, state)
        assert "252 SpA" in answer and len(calls) == 1
    # Mechanics and allocation failures share one retry budget rather than nesting loops.
    calls.clear()
    with pytest.raises(LLMProviderError):
        explain_champions_only(explain, bo3=True)
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
