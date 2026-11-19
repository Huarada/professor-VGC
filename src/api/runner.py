"""Execute validated analysis jobs without tying work to a browser connection."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.api.contracts import AnalysisInput
from src.api.jobs import JobStore
from src.api.projection import bo1_view, bo3_view, preview_view
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.exceptions import ProfessorVGCError
from src.domain.models import AnalysisRequest
from src.domain.series import Bo3AnalysisRequest
from src.services.container import Container

_LOG = logging.getLogger(__name__)


class AnalysisRunner:
    def __init__(self, store: JobStore, container_factory: Callable[[], Container] = Container) -> None:
        self.store = store
        self.container_factory = container_factory

    def run(self, job_id: str) -> None:
        if not self.store.claim(job_id):
            return
        job = self.store.get(job_id)
        if job is None:
            return
        inputs = AnalysisInput.model_validate(self.store.read_payload(job_id))
        container: Container | None = None
        try:
            container = self.container_factory()
            if inputs.mode == "bo1":
                self.store.update(job_id, stage="reviewing", detail="Checking the replay and damage evidence")
                bo1_request = AnalysisRequest(session_id=job["owner"], replay_raw_text=inputs.replay,
                                              question=inputs.question, provider=inputs.provider)
                self.store.update(job_id, paid_started=True)
                bo1_result = container.build_pipeline(inputs.provider, inputs.orchestrator, inputs.regulation).analyze(bo1_request)
                replay = container.parse_replay_for_viewer(inputs.replay)
                self.store.update(job_id, state="complete", stage="complete", detail="Analysis ready",
                                  result=bo1_view(bo1_result, replay))
            else:
                games: list[dict[str, Any] | str] = list(inputs.games)
                bo3_request = Bo3AnalysisRequest(session_id=job_id, games=games,
                                                 question=inputs.question, provider=inputs.provider,
                                                 teamsheet=inputs.teamsheet,
                                                 inference_options=InferenceOptions(
                                                     enabled=inputs.inference.enabled,
                                                     perspective=inputs.inference.perspective,
                                                     hp_mode=inputs.inference.hp_mode))

                def report(message: str) -> None:
                    stage = "inferring" if "Stage 1" in message else "reviewing"
                    self.store.update(job_id, stage=stage, detail=message)

                def deterministic_ready(evidence: object, inferred: object) -> None:
                    from src.domain.bo3_inference.models import Bo3InferenceEvidence
                    from src.domain.series import Bo3Evidence

                    assert isinstance(evidence, Bo3Evidence)
                    assert inferred is None or isinstance(inferred, Bo3InferenceEvidence)
                    self.store.update(job_id, stage="reviewing", detail="Verified battle evidence is ready; AI reviews continue",
                                      preview=preview_view(evidence, inferred), paid_started=True)

                bo3_result = container.build_bo3_pipeline(
                    inputs.provider, inputs.orchestrator, inputs.regulation,
                    progress=report, thinking_level=inputs.thinking_level,
                    on_deterministic_ready=deterministic_ready,
                ).analyze(bo3_request)
                replays = [container.parse_replay_for_viewer(text) for text in inputs.games]
                self.store.update(job_id, state="complete", stage="complete", detail="Series analysis ready",
                                  result=bo3_view(bo3_result, replays))
        except ProfessorVGCError as exc:
            self.store.update(job_id, state="failed", stage="failed", detail="Analysis failed", error=str(exc))
        except Exception:  # noqa: BLE001 - persist unexpected job failure and keep verified preview
            _LOG.exception("Analysis job %s failed", job_id)
            self.store.update(job_id, state="failed", stage="failed", detail="Analysis failed",
                              error="Analysis failed unexpectedly. Verified evidence, if available, remains below.")
        finally:
            if container is not None:
                container.shutdown()
