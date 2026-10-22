"""Allocation prose cannot replace the deterministic Champions inference tables."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
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
