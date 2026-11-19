"""Request-scoped BO3 evidence publication before game narration completes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from src.domain.models import AnalysisEvidence

_subscriber: ContextVar[Callable[[AnalysisEvidence], None] | None] = ContextVar(
    "bo3_evidence_subscriber", default=None,
)


@contextmanager
def publish_to(subscriber: Callable[[AnalysisEvidence], None]) -> Iterator[None]:
    """Scope one observer to one game review, including concurrent reviews."""
    token = _subscriber.set(subscriber)
    try:
        yield
    finally:
        _subscriber.reset(token)


def evidence_ready(evidence: AnalysisEvidence) -> None:
    """Notify the series coordinator; ordinary BO1 calls have no subscriber."""
    subscriber = _subscriber.get()
    if subscriber is not None:
        subscriber(evidence)
