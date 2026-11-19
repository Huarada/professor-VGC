"""Shared Cloud Run job store: Firestore metadata and private Storage artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from google.api_core.exceptions import PreconditionFailed
from google.cloud import firestore
from google.cloud.storage import Client as StorageClient

from src.api.jobs import ActiveAnalysisConflict, IdempotencyConflict
from src.domain.exceptions import UsageLimitExceededError


class CloudJobStore:
    def __init__(self, project: str, bucket: str, collection: str = "web_analysis_jobs",
                 quota_collection: str = "usage_quota") -> None:
        self.db: Any = firestore.Client(project=project)
        self.bucket: Any = StorageClient(project=project).bucket(bucket)
        self.jobs = self.db.collection(collection)
        self.owners = self.db.collection(f"{collection}_owners")
        self.quota = self.db.collection(quota_collection)

    @staticmethod
    def _id(owner: str, key: str) -> str:
        return hashlib.sha256(f"{owner}:{key}".encode()).hexdigest()[:40]

    def _upload(self, path: str, value: dict[str, Any], *, create_only: bool = False) -> None:
        blob = self.bucket.blob(path)
        try:
            blob.upload_from_string(json.dumps(value, separators=(",", ":")),
                                    content_type="application/json",
                                    if_generation_match=0 if create_only else None)
        except PreconditionFailed:
            if not create_only:
                raise

    def _download(self, path: str) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.bucket.blob(path).download_as_text()))

    def by_key(self, owner: str, key: str) -> dict[str, Any] | None:
        return self.get(self._id(owner, key), owner)

    def create(self, owner: str, key: str, request_hash: str,
               payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return self.create_with_quota(owner, key, request_hash, payload)

    def create_with_quota(self, owner: str, key: str, request_hash: str,
                          payload: dict[str, Any], *, quota_bucket: str | None = None,
                          quota_limit: int = 0) -> tuple[dict[str, Any], bool]:
        job_id = self._id(owner, key)
        payload_ref = f"web-jobs/{job_id}/request.json"
        self._upload(payload_ref, payload, create_only=True)
        job_ref = self.jobs.document(job_id)
        owner_ref = self.owners.document(owner)
        transaction = self.db.transaction()

        @firestore.transactional
        def insert(tx: Any) -> bool:
            existing: Any = job_ref.get(transaction=tx)
            if existing.exists:
                existing_data = existing.to_dict() or {}
                if existing_data["request_hash"] != request_hash:
                    raise IdempotencyConflict("Idempotency-Key was already used for another analysis.")
                return False
            lock: Any = owner_ref.get(transaction=tx)
            active_id = (lock.to_dict() or {}).get("active_id") if lock.exists else None
            if active_id:
                active: Any = self.jobs.document(active_id).get(transaction=tx)
                if active.exists and (active.to_dict() or {}).get("state") in ("queued", "running"):
                    raise ActiveAnalysisConflict("An analysis is already running in this browser session.")
            quota_ref = None
            if quota_bucket and quota_limit:
                # Read all candidates before any writes: Firestore transactions
                # cannot read after writing. The old Streamlit quota uses the
                # same slot documents, so both UIs share the daily allowance.
                candidates = [self.quota.document(f"{quota_bucket}_{n}")
                              for n in range(1, quota_limit + 1)]
                snapshots: list[Any] = [ref.get(transaction=tx) for ref in candidates]
                quota_ref = next((ref for ref, snap in zip(candidates, snapshots)
                                  if not snap.exists), None)
                if quota_ref is None:
                    raise UsageLimitExceededError(
                        f"Daily limit reached: {quota_limit} analyses per visitor per day "
                        "(resets at 00:00 UTC).")
            if quota_ref is not None:
                now = datetime.now(timezone.utc)
                tx.create(quota_ref, {"claimed_at": now, "expires_at": now + timedelta(days=2),
                                      "analysis_id": job_id})
            tx.create(job_ref, {"id": job_id, "owner": owner, "request_key": key,
                                "request_hash": request_hash, "payload_ref": payload_ref,
                                "state": "queued", "stage": "queued", "detail": "Waiting for an analysis worker",
                                "revision": 0, "error": None, "paid_started": False,
                                "preview_ref": None, "result_ref": None, "lease_until": None,
                                "created_at": firestore.SERVER_TIMESTAMP})
            tx.set(owner_ref, {"active_id": job_id})
            return True

        created = cast(bool, insert(transaction))
        record = self.get(job_id, owner)
        assert record is not None
        return record, created

    def get(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None:
        snapshot: Any = self.jobs.document(job_id).get()
        if not snapshot.exists:
            return None
        record = cast(dict[str, Any], snapshot.to_dict())
        if owner is not None and record["owner"] != owner:
            return None
        record["payload"] = None
        record["preview"] = {"ready": True} if record.get("preview_ref") else None
        record["result"] = {"ready": True} if record.get("result_ref") else None
        return record

    def read_payload(self, job_id: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return self._download(record["payload_ref"])

    def read_preview(self, job_id: str) -> dict[str, Any] | None:
        record = self.get(job_id)
        return self._download(record["preview_ref"]) if record and record.get("preview_ref") else None

    def read_result(self, job_id: str) -> dict[str, Any] | None:
        record = self.get(job_id)
        return self._download(record["result_ref"]) if record and record.get("result_ref") else None

    def claim(self, job_id: str) -> bool:
        ref = self.jobs.document(job_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def claim_one(tx: Any) -> bool:
            snapshot: Any = ref.get(transaction=tx)
            if not snapshot.exists:
                return False
            record = snapshot.to_dict() or {}
            now = datetime.now(timezone.utc)
            if record["state"] == "running":
                lease = record.get("lease_until")
                if lease is None or lease > now:
                    return False
                if record.get("paid_started"):
                    tx.update(ref, {"state": "failed", "stage": "failed",
                                    "detail": "Previous model call interrupted",
                                    "error": "The worker stopped during a model call. Verified evidence remains available; no automatic paid retry was made.",
                                    "revision": firestore.Increment(1)})
                    return False
            elif record["state"] != "queued":
                return False
            tx.update(ref, {"state": "running", "stage": "validating",
                            "detail": "Preparing verified battle evidence",
                            "lease_until": now + timedelta(minutes=20),
                            "revision": firestore.Increment(1)})
            return True

        return cast(bool, claim_one(transaction))

    def update(self, job_id: str, *, stage: str | None = None, detail: str | None = None,
               preview: dict[str, Any] | None = None, result: dict[str, Any] | None = None,
               state: str | None = None, error: str | None = None,
               paid_started: bool | None = None) -> None:
        changes: dict[str, Any] = {"revision": firestore.Increment(1)}
        if preview is not None:
            path = f"web-jobs/{job_id}/preview.json"
            self._upload(path, preview)
            changes["preview_ref"] = path
        if result is not None:
            path = f"web-jobs/{job_id}/result.json"
            self._upload(path, result)
            changes["result_ref"] = path
        for name, value in (("stage", stage), ("detail", detail), ("state", state),
                            ("error", error), ("paid_started", paid_started)):
            if value is not None:
                changes[name] = value
        self.jobs.document(job_id).update(changes)
