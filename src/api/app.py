"""React-facing API. Run locally with ``uvicorn src.api.app:app``."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from src.adapters.memory.firestore_conversation_memory import (
    FirestoreConversationMemory,
)
from src.adapters.memory.sqlite_conversation_memory import SqliteConversationMemory
from src.api.cloud_jobs import CloudJobStore
from src.api.cloud_queue import CloudTaskDispatcher, verify_task_token
from src.api.contracts import AnalysisInput, ImportInput
from src.api.jobs import (
    ActiveAnalysisConflict,
    IdempotencyConflict,
    JobStore,
    SqliteJobStore,
)
from src.api.runner import AnalysisRunner
from src.domain.exceptions import ProfessorVGCError, UsageLimitExceededError
from src.domain.interfaces import ConversationMemory
from src.services.container import Container

_ROOT = Path(__file__).resolve().parents[2]
_COOKIE = "pvgc_session"


def _owner(request: Request, response: Response) -> str:
    token = request.cookies.get(_COOKIE)
    if not token or len(token) < 32:
        token = secrets.token_urlsafe(32)
        response.set_cookie(_COOKIE, token, httponly=True,
                            secure=bool(os.getenv("K_SERVICE")) or request.url.scheme == "https",
                            samesite="lax", max_age=86400)
    return hashlib.sha256(token.encode()).hexdigest()


def _quota_visitor(request: Request, owner: str) -> str:
    # Never accept a visitor ID from the request body. Cloud Run's frontend appends the peer address
    # it saw as the LAST X-Forwarded-For entry; anything before it is client-supplied and spoofable.
    # Set PROFESSORVGC_TRUSTED_PROXY_HOPS to 2 behind an extra load balancer.
    hops = int(os.getenv("PROFESSORVGC_TRUSTED_PROXY_HOPS", "1"))
    parts = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(parts) >= hops >= 1:
        try:
            if ipaddress.ip_address(parts[-hops]).is_global:
                return parts[-hops]
        except ValueError:
            pass
    return request.client.host if request.client else owner


def _status(job: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "id": job["id"], "state": job["state"],
            "stage": job["stage"], "detail": job["detail"], "revision": job["revision"],
            "preview_ready": job["preview"] is not None, "result_ready": job["result"] is not None,
            "error": job["error"]}


def create_app(*, store: JobStore | None = None,
               container_factory: Callable[[], Container] | None = None,
               background: bool = True) -> FastAPI:
    backend = os.getenv("PROFESSORVGC_WEB_BACKEND", "local")
    role = os.getenv("PROFESSORVGC_WEB_ROLE", "api")
    if backend not in ("local", "cloud") or role not in ("api", "worker"):
        raise RuntimeError("Invalid PROFESSORVGC_WEB_BACKEND or PROFESSORVGC_WEB_ROLE.")
    if os.getenv("K_SERVICE") and backend != "cloud":
        raise RuntimeError("Cloud Run requires PROFESSORVGC_WEB_BACKEND=cloud.")
    dispatcher: CloudTaskDispatcher | None = None
    memory: ConversationMemory
    if backend == "cloud":
        project = os.getenv("PROFESSORVGC_FIRESTORE_PROJECT_ID", "")
        bucket = os.getenv("PROFESSORVGC_WEB_ARTIFACT_BUCKET", "")
        if not project or not bucket:
            raise RuntimeError("Cloud web jobs require a Firestore project and private artifact bucket.")
        store = store or CloudJobStore(project, bucket,
                                      quota_collection=os.getenv("PROFESSORVGC_USAGE_QUOTA_COLLECTION", "usage_quota"))
        memory = FirestoreConversationMemory(project)
        if role == "api" and background:
            dispatcher = CloudTaskDispatcher(
                project=project, location=os.getenv("PROFESSORVGC_WEB_TASK_LOCATION", ""),
                queue=os.getenv("PROFESSORVGC_WEB_TASK_QUEUE", ""),
                worker_url=os.getenv("PROFESSORVGC_WEB_WORKER_URL", ""),
                service_account_email=os.getenv("PROFESSORVGC_WEB_TASK_SERVICE_ACCOUNT", ""))
    else:
        store = store or SqliteJobStore(_ROOT / ".cache" / "web" / "jobs.sqlite3")
        if not isinstance(store, SqliteJobStore):
            raise RuntimeError("Local web jobs require a SQLite job store.")
        memory = SqliteConversationMemory(store.path)
    if container_factory is None:
        container_factory = lambda: Container(memory=memory)
    runner = AnalysisRunner(store, container_factory)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pvgc-web") if background and backend == "local" else None
    app = FastAPI(title="ProfessorVGC API", version="1.0.0")
    app.state.runner = runner
    app.state.store = store
    if executor is not None and isinstance(store, SqliteJobStore):
        for pending_id in store.recover_after_restart():
            executor.submit(runner.run, pending_id)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if role == "worker":
        audience = os.getenv("PROFESSORVGC_WEB_WORKER_URL", "")
        task_account = os.getenv("PROFESSORVGC_WEB_TASK_SERVICE_ACCOUNT", "")
        if backend == "cloud" and not (audience and task_account):
            raise RuntimeError("The cloud worker requires PROFESSORVGC_WEB_WORKER_URL and "
                               "PROFESSORVGC_WEB_TASK_SERVICE_ACCOUNT to verify Cloud Tasks tokens.")

        @app.post("/internal/run/{job_id}", status_code=204)
        def execute_task(job_id: str, request: Request) -> None:
            # Defense in depth: Cloud Run IAM should already keep this service private.
            if backend == "cloud" and not verify_task_token(
                    request.headers.get("authorization", ""), audience=audience, service_account_email=task_account):
                raise HTTPException(status_code=403, detail="Forbidden.")
            runner.run(job_id)

        return app

    @app.delete("/api/v1/conversation", status_code=204)
    def clear_conversation(request: Request, response: Response) -> None:
        memory.clear(_owner(request, response))
        response.set_cookie(_COOKIE, secrets.token_urlsafe(32), httponly=True,
                            secure=bool(os.getenv("K_SERVICE")) or request.url.scheme == "https",
                            samesite="lax", max_age=86400)

    @app.post("/api/v1/bo3/import")
    def import_bo3(body: ImportInput, request: Request, response: Response) -> dict[str, Any]:
        _owner(request, response)
        container = container_factory()
        try:
            imported = container.import_bo3_replays(body.source)
            return {"schema_version": 1, "series_id": imported.series_id,
                    "games": [{"number": game.number, "payload": game.payload,
                               "url": game.source_url or game.location.url,
                               "acquisition": game.acquisition} for game in imported.games],
                    "missing_games": list(imported.missing_games), "warnings": list(imported.warnings),
                    "teamsheet": imported.teamsheet.model_dump_json(indent=2) if imported.teamsheet else ""}
        except ProfessorVGCError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            container.shutdown()

    @app.post("/api/v1/analyses", status_code=202)
    def submit(body: AnalysisInput, request: Request, response: Response,
               idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=120)) -> dict[str, Any]:
        owner = _owner(request, response)
        request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        existing = store.by_key(owner, idempotency_key)
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency-Key was already used for another analysis.")
            if dispatcher is not None and existing["state"] == "queued":
                try:
                    dispatcher.dispatch(existing["id"])
                except Exception as exc:
                    raise HTTPException(status_code=503,
                                        detail="Analysis queued but dispatch failed; retry the same submission.") from exc
            return _status(existing)
        container = container_factory()
        try:
            if body.mode == "bo1":
                resolved = container.resolve_replay_text(body.replay.strip())
                validated = body.model_copy(update={"replay": resolved})
                from src.domain.models import AnalysisRequest

                container.validate_request(AnalysisRequest(session_id="validation", replay_raw_text=resolved,
                                                            question=body.question, provider=body.provider), body.regulation)
            else:
                from src.domain.bo3_inference.models import InferenceOptions
                from src.domain.series import Bo3AnalysisRequest

                games: list[dict[str, Any] | str] = [container.resolve_replay_text(game.strip())
                                                     for game in body.games]
                validated = body.model_copy(update={"games": games})
                container.validate_series(Bo3AnalysisRequest(
                    session_id="validation", games=games, question=body.question, provider=body.provider,
                    teamsheet=body.teamsheet, inference_options=InferenceOptions(
                        enabled=body.inference.enabled, perspective=body.inference.perspective,
                        hp_mode=body.inference.hp_mode)), body.regulation)
            try:
                if isinstance(store, CloudJobStore):
                    quota = container.usage_quota()
                    limit = quota.limit(body.provider)
                    job, created = store.create_with_quota(
                        owner, idempotency_key, request_hash, validated.model_dump(mode="json"),
                        quota_bucket=quota.bucket(body.provider, _quota_visitor(request, owner)) if limit else None,
                        quota_limit=limit)
                else:
                    job, created = store.create(owner, idempotency_key, request_hash,
                                                validated.model_dump(mode="json"))
            except (IdempotencyConflict, ActiveAnalysisConflict) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except UsageLimitExceededError as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc
            if created:
                if not isinstance(store, CloudJobStore):
                    try:
                        container.usage_quota().consume(body.provider, _quota_visitor(request, owner))
                        if isinstance(store, SqliteJobStore):
                            store.mark_quota_claimed(job["id"])
                    except ProfessorVGCError as exc:
                        store.update(job["id"], state="failed", stage="failed", detail="Usage quota unavailable", error=str(exc))
                        code = 429 if isinstance(exc, UsageLimitExceededError) else 503
                        raise HTTPException(status_code=code, detail=str(exc)) from exc
                if executor is not None:
                    try:
                        executor.submit(runner.run, job["id"])
                    except RuntimeError as exc:
                        store.update(job["id"], state="failed", stage="failed",
                                     detail="Analysis worker unavailable", error="Analysis worker unavailable.")
                        raise HTTPException(status_code=503, detail="Analysis worker unavailable.") from exc
                elif dispatcher is not None:
                    try:
                        dispatcher.dispatch(job["id"])
                    except Exception as exc:
                        raise HTTPException(status_code=503,
                                            detail="Analysis queued but dispatch failed; retry the same submission.") from exc
            return _status(store.get(job["id"], owner) or job)
        except ProfessorVGCError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            container.shutdown()

    def owned(job_id: str, request: Request) -> dict[str, Any]:
        token = request.cookies.get(_COOKIE)
        job = store.get(job_id, hashlib.sha256(token.encode()).hexdigest()) if token else None
        if job is None:
            raise HTTPException(status_code=404, detail="Analysis not found.")
        return job

    @app.get("/api/v1/analyses/{job_id}")
    def status(job_id: str, request: Request) -> dict[str, Any]:
        return _status(owned(job_id, request))

    @app.get("/api/v1/analyses/{job_id}/preview")
    def preview(job_id: str, request: Request) -> dict[str, Any]:
        owned(job_id, request)
        value = store.read_preview(job_id)
        if value is None:
            raise HTTPException(status_code=202, detail="Verified preview is still being calculated.")
        return value

    @app.get("/api/v1/analyses/{job_id}/result")
    def result(job_id: str, request: Request) -> dict[str, Any]:
        owned(job_id, request)
        value = store.read_result(job_id)
        if value is None:
            raise HTTPException(status_code=202, detail="Explanation is still running.")
        return value

    @app.get("/api/v1/analyses/{job_id}/games/{number}")
    def game(job_id: str, number: int, request: Request) -> dict[str, Any]:
        owned(job_id, request)
        result_body = store.read_result(job_id)
        if result_body is None:
            raise HTTPException(status_code=202, detail="Game review is still running.")
        games = result_body["games"] if result_body["mode"] == "bo3" else [result_body["game"]]
        if number < 1 or number > len(games):
            raise HTTPException(status_code=404, detail="Game not found.")
        return cast(dict[str, Any], games[number - 1])

    @app.get("/api/v1/analyses/{job_id}/evidence")
    def evidence(job_id: str, request: Request, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        owned(job_id, request)
        source = store.read_preview(job_id) or (store.read_result(job_id) or {}).get("preview")
        if source is None:
            raise HTTPException(status_code=202, detail="Damage evidence is still being calculated.")
        citations = (source.get("inference") or {}).get("damage_citations", [])
        start = max(offset, 0)
        return {"schema_version": 1, "total": len(citations), "items": citations[start:start + min(max(limit, 1), 100)]}

    dist = _ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
