"""Isolated BO3 game reviews run concurrently without changing order or isolation."""

import threading

import pytest

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.config import Settings
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.exceptions import LLMProviderError
from src.domain.models import AnalysisResult, ChatMessage, MetaContext, SelectionPlan
from src.domain.series import Bo3AnalysisRequest
from src.services.series_analysis import Bo3AnalysisService
from tests.conftest import PROMPTS, FakeLLM
from tests.test_bo3_inference import log


def series():
    return Bo3AnalysisRequest(session_id="parallel", games=[log(), log(winner="Bob", turn=2), log(turn=3)],
                              inference_options=InferenceOptions(enabled=False))


def service(pipeline, *, parallel, progress=None, memory=None):
    return Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=pipeline,
        llm=FakeLLM("{}", "Alice won the series."), prompts=PROMPTS,
        progress=progress, memory=memory, parallel_reviews=parallel)


def test_three_reviews_overlap_and_keep_game_order():
    # Each review waits until all three are running: a sequential loop would time out.
    barrier = threading.Barrier(3, timeout=10)
    progress = []
    memory = InMemoryConversationMemory()

    class Pipeline:
        def analyze(self, req):
            memory.append(req.session_id, ChatMessage(role="user", content="review"))
            barrier.wait()
            return AnalysisResult(session_id=req.session_id, question=req.question,
                answer=f"Game {req.series_context['game']}", selection=SelectionPlan(), meta_context=MetaContext())

    result = service(Pipeline, parallel=3, progress=progress.append, memory=memory).analyze(series())
    assert [game.answer for game in result.games] == ["Game 1", "Game 2", "Game 3"]
    assert any("Reviewing games 1-3 of 3 in parallel" in message for message in progress)
    assert all(not memory.load(game.session_id) for game in result.games)  # every isolated session cleared


def test_a_failed_parallel_review_names_its_game():
    class Pipeline:
        def analyze(self, req):
            if req.series_context["game"] == 2:
                raise LLMProviderError("Gemini request timed out")
            return AnalysisResult(session_id=req.session_id, question=req.question,
                answer="Game review", selection=SelectionPlan(), meta_context=MetaContext())

    with pytest.raises(LLMProviderError, match="BO3 game 2 review: Gemini request timed out"):
        service(Pipeline, parallel=3).analyze(series())


def test_parallel_reviews_setting_defaults_to_three_and_one_restores_sequential(monkeypatch):
    assert Settings().bo3_parallel_reviews == 3
    monkeypatch.setenv("PROFESSORVGC_BO3_PARALLEL_REVIEWS", "1")
    assert Settings().bo3_parallel_reviews == 1
    monkeypatch.setenv("PROFESSORVGC_BO3_PARALLEL_REVIEWS", "4")
    with pytest.raises(ValueError):
        Settings()
