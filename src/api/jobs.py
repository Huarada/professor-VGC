"""Durable local job store for API development and single-host deployment.

Production multi-instance deployment requires a shared store and external queue.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Protocol


class IdempotencyConflict(Exception):
    """A key was reused with a different request body."""


class ActiveAnalysisConflict(Exception):
    """An owner already has a running analysis."""


class JobStore(Protocol):
    def by_key(self, owner: str, key: str) -> dict[str, Any] | None: ...
    def create(self, owner: str, key: str, request_hash: str,
               payload: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...
    def get(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None: ...
    def claim(self, job_id: str) -> bool: ...
    def update(self, job_id: str, *, stage: str | None = None, detail: str | None = None,
               preview: dict[str, Any] | None = None, result: dict[str, Any] | None = None,
               state: str | None = None, error: str | None = None,
               paid_started: bool | None = None) -> None: ...
    def read_payload(self, job_id: str) -> dict[str, Any]: ...
    def read_preview(self, job_id: str) -> dict[str, Any] | None: ...
    def read_result(self, job_id: str) -> dict[str, Any] | None: ...


class SqliteJobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS analysis_jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                stage TEXT NOT NULL, detail TEXT NOT NULL, revision INTEGER NOT NULL,
                preview TEXT, result TEXT, error TEXT, paid_started INTEGER NOT NULL DEFAULT 0,
                quota_claimed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(owner, request_key))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(analysis_jobs)")}
            if "paid_started" not in columns:
                db.execute("ALTER TABLE analysis_jobs ADD COLUMN paid_started INTEGER NOT NULL DEFAULT 0")
            if "quota_claimed" not in columns:
                db.execute("ALTER TABLE analysis_jobs ADD COLUMN quota_claimed INTEGER NOT NULL DEFAULT 0")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def by_key(self, owner: str, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM analysis_jobs WHERE owner=? AND request_key=?", (owner, key)).fetchone()
        return self._record(row) if row else None

    def create(self, owner: str, key: str, request_hash: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM analysis_jobs WHERE owner=? AND request_key=?", (owner, key)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise IdempotencyConflict("Idempotency-Key was already used for another analysis.")
                return self._record(row), False
            active = db.execute("""SELECT id FROM analysis_jobs WHERE owner=?
                AND state IN ('queued','running') LIMIT 1""", (owner,)).fetchone()
            if active:
                raise ActiveAnalysisConflict("An analysis is already running in this browser session.")
            job_id = uuid.uuid4().hex
            db.execute("""INSERT INTO analysis_jobs
                (id,owner,request_key,request_hash,payload,state,stage,detail,revision)
                VALUES (?,?,?,?,?,'queued','queued','Waiting for an analysis worker',0)""",
                (job_id, owner, key, request_hash, json.dumps(payload)))
        return self.get(job_id, owner), True  # type: ignore[return-value]

    def get(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None:
        with self._connect() as db:
            if owner is None:
                row = db.execute("SELECT * FROM analysis_jobs WHERE id=?", (job_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM analysis_jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        return self._record(row) if row else None

    def read_payload(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return dict(job["payload"])

    def read_preview(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        return job["preview"] if job else None

    def read_result(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        return job["result"] if job else None

    def claim(self, job_id: str) -> bool:
        with self._connect() as db:
            changed = db.execute("""UPDATE analysis_jobs SET state='running',stage='validating',
                detail='Preparing verified battle evidence',revision=revision+1
                WHERE id=? AND state='queued' AND quota_claimed=1""", (job_id,)).rowcount
        return bool(changed)

    def mark_quota_claimed(self, job_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE analysis_jobs SET quota_claimed=1,revision=revision+1 WHERE id=? AND state='queued'",
                       (job_id,))

    def recover_after_restart(self) -> list[str]:
        """Keep verified previews, fail uncertain running work, resume unstarted jobs."""
        with self._connect() as db:
            db.execute("""UPDATE analysis_jobs SET state='failed',stage='failed',
                detail='Submission interrupted',error='The server restarted before the analysis was reserved.',
                revision=revision+1 WHERE state='queued' AND quota_claimed=0""")
            db.execute("""UPDATE analysis_jobs SET state='failed',stage='failed',
                detail='Analysis interrupted',error='The server restarted during analysis; verified evidence remains available.',
                revision=revision+1 WHERE state='running'""")
            queued = db.execute("SELECT id FROM analysis_jobs WHERE state='queued' AND quota_claimed=1").fetchall()
        return [row["id"] for row in queued]

    def update(self, job_id: str, *, stage: str | None = None, detail: str | None = None,
               preview: dict[str, Any] | None = None, result: dict[str, Any] | None = None,
               state: str | None = None, error: str | None = None,
               paid_started: bool | None = None) -> None:
        values: list[Any] = []
        changes = []
        for name, value in (("stage", stage), ("detail", detail), ("preview", preview),
                            ("result", result), ("state", state), ("error", error)):
            if value is not None:
                changes.append(f"{name}=?")
                values.append(json.dumps(value) if name in ("preview", "result") else value)
        if paid_started is not None:
            changes.append("paid_started=?")
            values.append(int(paid_started))
        if not changes:
            return
        values.append(job_id)
        with self._connect() as db:
            db.execute(f"UPDATE analysis_jobs SET {', '.join(changes)},revision=revision+1 WHERE id=?", values)

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for name in ("payload", "preview", "result"):
            if record[name] is not None:
                record[name] = json.loads(record[name])
        return record
