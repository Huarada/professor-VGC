"""HTTP contract, idempotency, ownership and progressive BO3 output without paid calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from src.adapters.memory.sqlite_conversation_memory import SqliteConversationMemory
from src.api.app import create_app
from src.api.jobs import SqliteJobStore
from src.domain.bo3_inference.models import (
    STATS,
    Bo3InferenceEvidence,
    CandidateSummary,
    DamageCitation,
    HPReading,
    InferenceCheckpoint,
    PointRange,
)
from src.domain.exceptions import LLMProviderError, SeriesValidationError
from src.domain.models import AnalysisResult, ChatMessage, MetaContext, SelectionPlan
from src.domain.replay_view_models import BattleReplay, ReplayTurnSnapshot
from src.domain.series import Bo3AnalysisResult, Bo3Evidence, SeriesGameEvidence
from src.domain.series_import import ImportedReplay, ReplayLocation, SeriesImportResult


class _Quota:
    claims = 0

    def consume(self, provider: str, visitor: str) -> None:
        assert provider == "gemini" and visitor
        self.claims += 1


class _Container:
    quota = _Quota()
    fail_explanation = False

    def shutdown(self) -> None:
        pass

    def resolve_replay_text(self, text: str) -> str:
        return text

    def import_bo3_replays(self, source: str) -> SeriesImportResult:
        assert source
        return SeriesImportResult(games=tuple(ImportedReplay(
            location=ReplayLocation(kind="replay", id=f"game-{n}", url=f"https://example.test/game-{n}"),
            payload=f"game {n}", number=n, players=("Alice", "Bob"),
            format_id="gen9championsvgc2026regmc") for n in (1, 2)), series_id="example-series")

    def validate_request(self, request: Any, regulation: str) -> None:
        if "invalid" in (request.replay_raw_text or ""):
            raise SeriesValidationError("Invalid replay")

    def validate_series(self, request: Any, regulation: str) -> None:
        if len(request.games) > 3:
            raise SeriesValidationError("Too many games")

    def usage_quota(self) -> _Quota:
        return self.quota

    def parse_replay_for_viewer(self, text: str) -> BattleReplay:
        return BattleReplay(player_names={"p1": "Alice", "p2": "Bob"},
                            snapshots=[ReplayTurnSnapshot(turn=0, active={"p1": ["Gengar"], "p2": ["Floette"]})])

    def build_pipeline(self, provider: str, orchestrator: str, regulation: str) -> Any:
        class Pipeline:
            def analyze(self, request: Any) -> AnalysisResult:
                return AnalysisResult(session_id=request.session_id, question=request.question,
                                      answer="Verified BO1 explanation", selection=SelectionPlan(),
                                      meta_context=MetaContext())
        return Pipeline()

    def build_bo3_pipeline(self, provider: str, orchestrator: str, regulation: str, **kwargs: Any) -> Any:
        outer = self

        class Pipeline:
            def analyze(self, request: Any) -> Bo3AnalysisResult:
                evidence = Bo3Evidence(format_id="gen9championsvgc2026regmc", players=["Alice", "Bob"],
                                       games=[SeriesGameEvidence(number=n, winner="Alice",
                                                                 score_before={"Alice": n - 1, "Bob": 0},
                                                                 score_after={"Alice": n, "Bob": 0},
                                                                 players_by_side={"p1": "Alice", "p2": "Bob"},
                                                                 leads={}, brought={}, revealed={})
                                              for n in range(1, len(request.games) + 1)],
                                       score={"Alice": len(request.games), "Bob": 0},
                                       complete=len(request.games) == 2)
                kwargs["progress"]("Stage 1 of 3 - Checking damage")
                inferred = _inference()
                kwargs["on_deterministic_ready"](evidence, inferred)
                if outer.fail_explanation:
                    raise LLMProviderError("Explanation provider unavailable")
                reviews = [AnalysisResult(session_id=f"{request.session_id}:{n}", question=request.question,
                                          answer=f"Game {n} verified review", selection=SelectionPlan(),
                                          meta_context=MetaContext()) for n in range(1, len(request.games) + 1)]
                return Bo3AnalysisResult(session_id=request.session_id, answer="Series comparison is ready",
                                         evidence=evidence, games=reviews, inference=inferred)
        return Pipeline()


def _inference() -> Bo3InferenceEvidence:
    baseline = {stat: PointRange(minimum=0, maximum=32) for stat in STATS}
    current = {**baseline, "atk": PointRange(minimum=30, maximum=32)}
    summary = CandidateSummary(pokemon_id="Alice:slot1", species="Garchomp", stat_points=current,
                               baseline_stat_points=baseline, natures=("Jolly",), basis="necessary_local_bounds")
    checkpoint = InferenceCheckpoint(id="after-2", game=2, phase="after_game", perspective="public",
                                     outcome="ambiguous", coverage="bounded", observation_ids=("g2:e43",),
                                     local_constraints=("g2:e43",), summaries=(summary,))
    citation = DamageCitation(id="g2:e43", game=2, event_index=43,
                              attacker_id="Alice:slot1", defender_id="Bob:slot1",
                              attacker_species="Garchomp", defender_species="Floette-Eternal",
                              move="Dragon Claw", before=HPReading(numerator=100, denominator=100, mode="percent"),
                              after=HPReading(numerator=40, denominator=100, mode="percent"),
                              dependent_axes={"Alice:slot1": ("atk",)})
    return Bo3InferenceEvidence(continuity="registered_team", checkpoints=(checkpoint,),
                                damage_citations=(citation,))


def _body(mode: str = "bo1") -> dict[str, Any]:
    return {"mode": mode, "provider": "gemini", "orchestrator": "native", "regulation": "auto",
            "question": "What happened?", "replay": "valid replay" if mode == "bo1" else "",
            "games": [] if mode == "bo1" else ["game 1", "game 2"]}


def _app(tmp_path: Path) -> Any:
    _Container.quota = _Quota()
    _Container.fail_explanation = False
    return create_app(store=SqliteJobStore(tmp_path / "jobs.sqlite3"), container_factory=_Container, background=False)


def test_bo1_http_result_idempotency_and_job_ownership(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/analyses", json=_body(), headers={"Idempotency-Key": "one-click-001"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        assert client.get(f"/api/v1/analyses/{job_id}/result").status_code == 202
        app.state.runner.run(job_id)
        result = client.get(f"/api/v1/analyses/{job_id}/result")
        assert result.status_code == 200
        assert result.json()["answer"] == "Verified BO1 explanation"
        assert result.json()["game"]["replay"]["snapshots"][0]["active"]["p1"] == ["Gengar"]
        again = client.post("/api/v1/analyses", json=_body(), headers={"Idempotency-Key": "one-click-001"})
        assert again.json()["id"] == job_id
        assert _Container.quota.claims == 1
        changed = client.post("/api/v1/analyses", json=_body("bo3"), headers={"Idempotency-Key": "one-click-001"})
        assert changed.status_code == 409
    with TestClient(app) as stranger:
        assert stranger.get(f"/api/v1/analyses/{job_id}").status_code == 404


def test_bo3_preview_survives_provider_failure(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _Container.fail_explanation = True
    with TestClient(app) as client:
        started = client.post("/api/v1/analyses", json=_body("bo3"), headers={"Idempotency-Key": "series-click-001"})
        assert started.status_code == 202
        job_id = started.json()["id"]
        app.state.runner.run(job_id)
        status = client.get(f"/api/v1/analyses/{job_id}").json()
        assert status["state"] == "failed" and status["preview_ready"]
        preview = client.get(f"/api/v1/analyses/{job_id}/preview").json()
        assert preview["evidence"]["score"] == {"Alice": 2, "Bob": 0}
        assert preview["evidence"]["complete"] is True
        finding = preview["inference"]["checkpoints"][0]["pokemon"][0]
        assert finding["constrained_native_bounds"]["atk"] == {"minimum": 30, "maximum": 32}
        assert finding["range_reductions"]["atk"]["reduction_percent"] == 90.9
        assert preview["inference"]["damage_citations"][0]["move"] == "Dragon Claw"
        assert client.get(f"/api/v1/analyses/{job_id}/result").status_code == 202


def test_invalid_input_does_not_consume_quota(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        body = _body()
        body["replay"] = "invalid replay"
        response = client.post("/api/v1/analyses", json=body, headers={"Idempotency-Key": "invalid-001"})
        assert response.status_code == 422
        assert _Container.quota.claims == 0


def test_bo3_import_is_free_and_returns_ordered_games(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/bo3/import", json={"source": "example room"})
        assert response.status_code == 200
        assert [game["number"] for game in response.json()["games"]] == [1, 2]
        assert [game["payload"] for game in response.json()["games"]] == ["game 1", "game 2"]
        assert _Container.quota.claims == 0


def test_unreserved_local_job_cannot_start_after_restart(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.sqlite3")
    job, created = store.create("owner", "interrupted-key", "request-hash", _body())
    assert created
    assert store.claim(job["id"]) is False
    assert store.recover_after_restart() == []
    assert store.get(job["id"])["state"] == "failed"


def test_local_conversation_survives_restart_and_reset_rotates_session(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    first = SqliteConversationMemory(path)
    first.append("owner-one", ChatMessage(role="user", content="Remember game one"))
    assert SqliteConversationMemory(path).load("owner-one")[0].content == "Remember game one"
    app = _app(tmp_path)
    with TestClient(app) as client:
        started = client.post("/api/v1/analyses", json=_body(), headers={"Idempotency-Key": "before-reset-001"})
        assert started.status_code == 202
        original_cookie = client.cookies.get("pvgc_session")
        other = client.post("/api/v1/analyses", json=_body(), headers={"Idempotency-Key": "another-click-001"})
        assert other.status_code == 409
        app.state.runner.run(started.json()["id"])
        assert client.delete("/api/v1/conversation").status_code == 204
        assert client.cookies.get("pvgc_session") != original_cookie
        assert client.get(f"/api/v1/analyses/{started.json()['id']}").status_code == 404


def test_quota_visitor_ignores_client_supplied_forwarded_for(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from src.api.app import _quota_visitor

    def visitor(header: str) -> str:
        request = SimpleNamespace(headers={"x-forwarded-for": header}, client=SimpleNamespace(host="10.0.0.1"))
        return _quota_visitor(request, "owner")  # type: ignore[arg-type]

    assert visitor("8.8.8.8, 1.1.1.1") == "1.1.1.1"      # spoofed prefix is ignored
    assert visitor("1.1.1.1, 10.0.0.2") == "10.0.0.1"    # last hop private: do not fall back to a spoofable one
    monkeypatch.setenv("PROFESSORVGC_TRUSTED_PROXY_HOPS", "2")
    assert visitor("8.8.8.8, 1.1.1.1, 9.9.9.9") == "1.1.1.1"


def test_cloud_worker_rejects_unauthenticated_and_foreign_tokens(monkeypatch: Any) -> None:
    import src.api.app as web

    monkeypatch.setattr(web, "CloudJobStore", lambda *a, **k: _Store())
    monkeypatch.setattr(web, "FirestoreConversationMemory", lambda *_: None)
    for key, value in {"PROFESSORVGC_WEB_BACKEND": "cloud", "PROFESSORVGC_WEB_ROLE": "worker",
                       "PROFESSORVGC_FIRESTORE_PROJECT_ID": "p", "PROFESSORVGC_WEB_ARTIFACT_BUCKET": "b",
                       "PROFESSORVGC_WEB_WORKER_URL": "https://worker.example",
                       "PROFESSORVGC_WEB_TASK_SERVICE_ACCOUNT": "tasks@p.iam.gserviceaccount.com"}.items():
        monkeypatch.setenv(key, value)
    def reject(*_a: Any, **_k: Any) -> None:
        raise ValueError("bad token")

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", reject)
    ran: list[str] = []
    app = create_app(store=_Store(), container_factory=lambda: None, background=False)  # type: ignore[arg-type,return-value]
    app.state.runner.run = ran.append
    client = TestClient(app)
    assert client.post("/internal/run/abc").status_code == 403
    assert client.post("/internal/run/abc", headers={"Authorization": "Bearer forged"}).status_code == 403
    monkeypatch.setattr(web, "verify_task_token", lambda *a, **k: True)
    assert client.post("/internal/run/abc").status_code == 204 and ran == ["abc"]


class _Store:
    pass
