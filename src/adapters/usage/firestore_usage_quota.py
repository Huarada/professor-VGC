"""Firestore-backed :class:`UsageQuotaStore`.

Each slot is its own document, ``{collection}/{bucket}_{n}``, claimed with
``create()`` — a server-side "only if it does not exist yet" write. That makes
a claim atomic without a transaction: two concurrent analyses racing for the
last slot both try to create the same document id and exactly one wins.

Every document carries ``expires_at`` (two days after the claim), so a
Firestore TTL policy on that field can delete old slots automatically; the
quota itself never reads old days, so cleanup is housekeeping only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.domain.exceptions import UsageQuotaError


class FirestoreUsageQuotaStore:
    """``UsageQuotaStore`` over a Firestore collection."""

    def __init__(self, client: Any, collection: str = "usage_quota") -> None:
        self._client = client
        self._collection = collection

    def claim(self, bucket: str, slots: int) -> int | None:
        from google.api_core.exceptions import AlreadyExists

        now = datetime.now(timezone.utc)
        for slot in range(1, slots + 1):
            ref = self._client.collection(self._collection).document(f"{bucket}_{slot}")
            try:
                ref.create({"claimed_at": now, "expires_at": now + timedelta(days=2)})
            except AlreadyExists:
                continue
            except Exception as exc:  # noqa: BLE001 - many concrete gRPC/auth exception types
                raise UsageQuotaError(f"Unable to record usage in Firestore: {exc}") from exc
            return slot
        return None

    def claimed(self, bucket: str, slots: int) -> int:
        try:
            return sum(
                1
                for slot in range(1, slots + 1)
                if self._client.collection(self._collection)
                .document(f"{bucket}_{slot}")
                .get()
                .exists
            )
        except Exception as exc:  # noqa: BLE001 - many concrete gRPC/auth exception types
            raise UsageQuotaError(f"Unable to read usage from Firestore: {exc}") from exc
