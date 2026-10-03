"""Per-visitor daily usage quota (ADR-036): the service's limits, buckets and
HMAC'd visitor keys over an in-memory store, the Firestore store's atomic
slot claims over a fake client, and the Container wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from google.api_core.exceptions import AlreadyExists, ServiceUnavailable

from src.adapters.usage.firestore_usage_quota import FirestoreUsageQuotaStore
from src.config import Settings
from src.domain.exceptions import ConfigurationError, UsageLimitExceededError, UsageQuotaError
from src.domain.interfaces import UsageQuotaStore
from src.services.container import Container
from src.services.usage_quota import UsageQuotaService


class InMemoryQuotaStore:
    """``UsageQuotaStore`` fake: slots are just a set of taken (bucket, n)."""

    def __init__(self) -> None:
        self.taken: set[tuple[str, int]] = set()

    def claim(self, bucket: str, slots: int) -> int | None:
        for slot in range(1, slots + 1):
            if (bucket, slot) not in self.taken:
                self.taken.add((bucket, slot))
                return slot
        return None

    def claimed(self, bucket: str, slots: int) -> int:
        return sum((bucket, slot) in self.taken for slot in range(1, slots + 1))


def _clock(day: int) -> datetime:
    return datetime(2026, 10, day, 23, 59, tzinfo=timezone.utc)


def _service(store: InMemoryQuotaStore, *, day: int = 2, limit: int = 4) -> UsageQuotaService:
    return UsageQuotaService(store, {"openai": limit}, secret="s3cret", clock=lambda: _clock(day))


def test_in_memory_store_satisfies_the_port():
    assert isinstance(InMemoryQuotaStore(), UsageQuotaStore)


def test_four_analyses_then_refused():
    quota = _service(InMemoryQuotaStore())
    assert quota.remaining("openai", "203.0.113.7") == 4
    assert [quota.consume("openai", "203.0.113.7") for _ in range(4)] == [3, 2, 1, 0]
    with pytest.raises(UsageLimitExceededError, match="4 openai analyses"):
        quota.consume("openai", "203.0.113.7")
    assert quota.remaining("openai", "203.0.113.7") == 0


def test_quota_is_per_visitor_and_per_day():
    store = InMemoryQuotaStore()
    today = _service(store, day=2)
    for _ in range(4):
        today.consume("openai", "203.0.113.7")
    assert today.remaining("openai", "198.51.100.9") == 4  # another visitor
    assert _service(store, day=3).remaining("openai", "203.0.113.7") == 4  # next UTC day


def test_unlimited_provider_never_touches_the_store():
    store = InMemoryQuotaStore()
    quota = _service(store)
    assert quota.limit("gemini") == 0
    assert quota.consume("gemini", "203.0.113.7") is None
    assert quota.remaining("gemini", "203.0.113.7") is None
    assert UsageQuotaService(None, {"openai": 4}, secret="x").consume("openai", "v") is None
    assert store.taken == set()


def test_visitor_is_stored_only_as_an_hmac():
    store = InMemoryQuotaStore()
    _service(store).consume("openai", "203.0.113.7")
    ((bucket, _),) = store.taken
    assert "203.0.113.7" not in bucket
    assert bucket.startswith("2026-10-02_openai_")
    other_secret = UsageQuotaService(store, {"openai": 4}, secret="other", clock=lambda: _clock(2))
    assert other_secret.remaining("openai", "203.0.113.7") == 4  # key depends on the secret


# --- Firestore store over a fake client ----------------------------------- #


class _FakeDoc:
    def __init__(self, db: _FakeDb, doc_id: str) -> None:
        self._db, self._id = db, doc_id

    def create(self, data: dict[str, Any]) -> None:
        if self._db.fail:
            raise ServiceUnavailable("firestore down")
        if self._id in self._db.docs:
            raise AlreadyExists(f"{self._id} exists")
        self._db.docs[self._id] = data

    def get(self) -> Any:
        if self._db.fail:
            raise ServiceUnavailable("firestore down")
        return type("Snap", (), {"exists": self._id in self._db.docs})()


class _FakeDb:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.fail = False
        self.collections: set[str] = set()

    def collection(self, name: str) -> _FakeDb:
        self.collections.add(name)
        return self

    def document(self, doc_id: str) -> _FakeDoc:
        return _FakeDoc(self, doc_id)


def test_firestore_store_claims_lowest_free_slot_atomically():
    db = _FakeDb()
    store = FirestoreUsageQuotaStore(db, collection="usage_quota")
    assert [store.claim("b", 2) for _ in range(3)] == [1, 2, None]
    assert store.claimed("b", 2) == 2
    assert set(db.docs) == {"b_1", "b_2"}
    assert db.collections == {"usage_quota"}
    assert {"claimed_at", "expires_at"} <= set(db.docs["b_1"])


def test_firestore_store_fails_closed():
    db = _FakeDb()
    db.fail = True
    store = FirestoreUsageQuotaStore(db)
    with pytest.raises(UsageQuotaError):
        store.claim("b", 4)
    with pytest.raises(UsageQuotaError):
        store.claimed("b", 4)


# --- Container wiring ------------------------------------------------------ #


def test_container_without_a_limit_needs_no_firestore():
    container = Container(Settings(_env_file=None, firestore_project_id=None))
    quota = container.usage_quota()
    assert quota.limit("openai") == 0
    assert container.usage_quota() is quota


def test_container_requires_a_secret_when_a_limit_is_set():
    container = Container(Settings(_env_file=None, openai_daily_analysis_limit=4))
    with pytest.raises(ConfigurationError, match="USAGE_QUOTA_SECRET"):
        container.usage_quota()


def test_negative_limit_is_rejected_by_settings():
    with pytest.raises(ValueError):
        Settings(_env_file=None, openai_daily_analysis_limit=-1)
