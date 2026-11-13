"""Isolated BO3 game reviews run concurrently without changing order or isolation."""

import threading

import pytest

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.config import Settings
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.exceptions import LLMProviderError
from src.domain.models import AnalysisEvidence, AnalysisResult, ChatMessage, MetaContext, SelectionPlan
from src.services.bo3_evidence_publication import evidence_ready
from src.services.ground_truth import build_result
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
    assert [timing.game for timing in result.review_timings] == [1, 2, 3]
    assert result.reviews_wall_seconds is not None and result.reviews_wall_seconds > 0
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


def test_series_summary_starts_from_ordered_evidence_before_game_narration_finishes():
    summary_started = threading.Event()
    game_explanations_waiting = threading.Barrier(3, timeout=10)

    class SummaryLLM:
        def __init__(self):
            self.messages = []

        def complete(self, *, messages, **kwargs):
            self.messages = messages
            summary_started.set()
            return "Alice won the series."

    class Pipeline:
        def analyze(self, req):
            number = req.series_context["game"]
            facts = AnalysisEvidence(selection=SelectionPlan(), meta_context=MetaContext(),
                                     battle_result=f"verified game {number}")
            evidence_ready(facts)
            game_explanations_waiting.wait()
            assert summary_started.wait(timeout=10)
            return build_result(request=req, evidence=facts, answer=f"Game {number}", provider="fake")

    llm = SummaryLLM()
    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
        llm=llm, prompts=PROMPTS, parallel_reviews=3).analyze(series())
    import json

    payload = json.loads(llm.messages[-1].content)
    assert [game["battle_result"] for game in payload["game_evidence"]] == [
        "verified game 1", "verified game 2", "verified game 3"]
    assert [game.answer for game in result.games] == ["Game 1", "Game 2", "Game 3"]


def test_overlap_opt_out_waits_for_game_explanations():
    completed = []
    lock = threading.Lock()

    class SummaryLLM:
        def complete(self, **kwargs):
            assert len(completed) == 3
            return "Alice won the series."

    class Pipeline:
        def analyze(self, req):
            number = req.series_context["game"]
            facts = AnalysisEvidence(selection=SelectionPlan(), meta_context=MetaContext())
            evidence_ready(facts)
            with lock:
                completed.append(number)
            return build_result(request=req, evidence=facts, answer=f"Game {number}", provider="fake")

    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
        llm=SummaryLLM(), prompts=PROMPTS, parallel_reviews=3,
        overlap_synthesis=False).analyze(series())
    assert result.answer == "Alice won the series."


def test_overlapped_and_sequential_summary_receive_the_same_game_evidence():
    import json

    def summary_payload(overlap):
        class SummaryLLM:
            def __init__(self):
                self.payload = None

            def complete(self, *, messages, **kwargs):
                self.payload = json.loads(messages[-1].content)
                return "Alice won the series."

        class Pipeline:
            def analyze(self, req):
                number = req.series_context["game"]
                facts = AnalysisEvidence(selection=SelectionPlan(), meta_context=MetaContext(),
                                         battle_result=f"verified game {number}")
                evidence_ready(facts)
                return build_result(request=req, evidence=facts, answer=f"Game {number}", provider="fake")

        llm = SummaryLLM()
        Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
            llm=llm, prompts=PROMPTS, parallel_reviews=3,
            overlap_synthesis=overlap).analyze(series())
        return llm.payload

    assert summary_payload(True) == summary_payload(False)


def test_parallel_reviews_setting_defaults_to_three_and_one_restores_sequential(monkeypatch):
    assert Settings().bo3_parallel_reviews == 3
    assert Settings().bo3_overlap_synthesis is True
    monkeypatch.setenv("PROFESSORVGC_BO3_PARALLEL_REVIEWS", "1")
    assert Settings().bo3_parallel_reviews == 1
    monkeypatch.setenv("PROFESSORVGC_BO3_OVERLAP_SYNTHESIS", "false")
    assert Settings().bo3_overlap_synthesis is False
    monkeypatch.setenv("PROFESSORVGC_BO3_PARALLEL_REVIEWS", "4")
    with pytest.raises(ValueError):
        Settings()
