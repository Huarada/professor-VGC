"""Progress follows actual BO3 operations, never a timer or financial wording."""

from unittest.mock import Mock, patch

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.models import AnalysisResult, MetaContext, SelectionPlan
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import Bo3AnalysisService, prepare_series
from src.ui.loading import loading_overlay
from tests.conftest import PROMPTS, FakeLLM
from tests.test_bo3_analysis import game, request
from tests.test_bo3_inference import AnalyticProjection, log, sheet


def test_review_and_summary_progress_is_emitted_before_each_real_operation():
    progress = []

    class Pipeline:
        def analyze(self, req):
            number = req.series_context["game"]
            assert progress[-1].startswith(f"Stage 2 of 3 — Reviewing game {number} of 2:")
            assert "turn-by-turn decisions and damage evidence" in progress[-1]
            return AnalysisResult(session_id=req.session_id, question=req.question,
                                  answer="Game review", selection=SelectionPlan(), meta_context=MetaContext())

    class LLM(FakeLLM):
        def complete(self, **kwargs):
            assert progress[-1].startswith("Stage 3 of 3 — AI comparing games:")
            return super().complete(**kwargs)

    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
        llm=LLM("{}", "Alice won the series."), prompts=PROMPTS,
        progress=progress.append).analyze(request(game(), game(move="Protect")))
    assert len(result.games) == 2
    assert progress[0].startswith("Stage 1 of 3 — Checking replay order")
    assert progress[-1].startswith("Stage 3 of 3 — Checking the final explanation")
    assert not any("investment" in message.lower() for message in progress)
    assert not any("Checking stat spreads" in message for message in progress)


def test_inference_progress_distinguishes_games_and_runs_before_engine_batches():
    progress = []

    class Engine(AnalyticProjection):
        def project_batch(self, requests):
            assert progress[-1].startswith("Stage 1 of 3 — Game ")
            return super().project_batch(requests)

    req = Bo3AnalysisRequest(session_id="progress", games=[log(), log(winner="Bob", damage=61)],
        teamsheet=sheet(), inference_options=InferenceOptions(hp_mode="exact"))
    parser = ShowdownReplayParser()
    evidence, states = prepare_series(parser, req)
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=Engine(),
        progress=progress.append).analyze(req, evidence, states)
    assert result is not None
    for number in (1, 2):
        reading = next(i for i, message in enumerate(progress) if f"Game {number} of 2: reconstructing HP" in message)
        checking = next(i for i, message in enumerate(progress) if f"Game {number} of 2: matching" in message)
        assert reading < checking


def test_overlay_updates_the_existing_placeholder_and_removes_previous_caption():
    placeholder = Mock()
    with patch("streamlit.empty", return_value=placeholder):
        with loading_overlay(title="Analysing your BO3", steps=("Reading replays...",)) as progress:
            progress("Reviewing game 1 of 2...")
            progress("Writing the BO3 review...")
    frames = [call.args[0] for call in placeholder.markdown.call_args_list]
    assert len(frames) == 3
    assert "Reviewing game 1 of 2" in frames[1]
    assert "Reviewing game 1 of 2" not in frames[2]
    assert "Writing the BO3 review" in frames[2]
    placeholder.empty.assert_called_once()


def test_three_stage_progress_covers_all_three_games_and_matches_completed_engine_work():
    progress = []
    ready = []

    class Pipeline:
        def analyze(self, req):
            assert ready and ready[0][1] is not None
            assert progress[-1].startswith(f"Stage 2 of 3 — Reviewing game {req.series_context['game']} of 3:")
            return AnalysisResult(session_id=req.session_id, question=req.question,
                answer="Game review", selection=SelectionPlan(), meta_context=MetaContext())

    req = Bo3AnalysisRequest(session_id="three-stages", games=[log(), log(winner="Bob", turn=2), log(turn=3)],
        teamsheet=sheet(), inference_options=InferenceOptions(hp_mode="exact"))
    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
        llm=FakeLLM("{}", "Alice won the series."), prompts=PROMPTS,
        inference=Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection(), progress=progress.append),
        progress=progress.append,
        on_deterministic_ready=lambda evidence, inferred: ready.append((evidence, inferred))).analyze(req)
    assert len(result.games) == 3
    assert ready[0][0] == result.evidence and ready[0][1] == result.inference
    for number in (1, 2, 3):
        assert any(f"Stage 1 of 3 — Game {number} of 3: matching" in message for message in progress)
        assert any(f"Stage 2 of 3 — Reviewing game {number} of 3:" in message for message in progress)
    phases = [int(message.split(" of 3")[0].removeprefix("Stage ")) for message in progress]
    assert phases == sorted(phases) and set(phases) == {1, 2, 3}
    assert any("comparing Garchomp's Dragon Claw" in message for message in progress)
    assert any("fully checked damage comparisons" in message for message in progress)
    assert any("Establishing the opening plan" in message for message in progress)
    assert any("revealed in game 1" in message for message in progress)
    assert any("evidence from games 1 and 2" in message for message in progress)


def test_calculator_progress_counts_real_completed_work_and_throttles_updates(monkeypatch):
    from src.domain.bo3_inference.models import HPReading, ProjectionRequest
    from src.domain.models import CalcRequest, PokemonSet
    from src.services.bo3_inference.solver import ProjectionBudget

    clock = [100.0]
    monkeypatch.setattr("src.services.bo3_inference.solver.time.monotonic", lambda: clock[0])
    budget = ProjectionBudget(AnalyticProjection(), InferenceOptions())
    messages = []
    budget.progress = messages.append
    budget.progress_context = "Stage 1 of 3 — Game 2 of 3"
    req = ProjectionRequest(calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp"),
        defender=PokemonSet(species="Incineroar"), move="Dragon Claw"),
        before=HPReading(numerator=100, denominator=100, mode="exact"),
        attacker_before=HPReading(numerator=100, denominator=100, mode="exact"))
    budget.project([req])
    assert "Completed damage checks: 0" in messages[-1]
    budget.project([req])  # cache hit: no extra native work or immediate UI update
    assert len(messages) == 1 and budget.projections == 1
    clock[0] += 1
    budget.project([req])
    assert len(messages) == 2 and "Completed damage checks: 1" in messages[-1]


def test_failed_engine_attempts_do_not_inflate_completed_progress_count():
    from src.domain.bo3_inference.models import HPReading, ProjectionRequest
    from src.domain.exceptions import CalcEngineError
    from src.domain.models import CalcRequest, PokemonSet
    from src.services.bo3_inference.solver import ProjectionBudget

    engine = AnalyticProjection()
    engine.project_batch = Mock(side_effect=CalcEngineError("Worker unavailable"))
    budget = ProjectionBudget(engine, InferenceOptions())
    req = ProjectionRequest(calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp"),
        defender=PokemonSet(species="Incineroar"), move="Dragon Claw"),
        before=HPReading(numerator=100, denominator=100, mode="exact"),
        attacker_before=HPReading(numerator=100, denominator=100, mode="exact"))
    result = budget.project([req])
    assert not result[0].supported
    assert budget.projections == 1 and budget.completed_checks == 0
