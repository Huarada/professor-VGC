"""Request-local BO3 review timings shared by ADK and LangChain.

No prompt, tool argument, model response or player data enters a timing record.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any
from uuid import UUID

from src.domain.series import Bo3ReviewTiming

_ACTIVE: ContextVar[ReviewProfiler | None] = ContextVar("bo3_review_profiler", default=None)


class ReviewProfiler:
    """Mutable counters owned by one game review; callbacks can use threads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._stages: dict[str, float] = defaultdict(float)
        self._model_started: dict[str, float] = {}
        self._tool_started: dict[str, tuple[str, float]] = {}
        self.model_calls = 0
        self.model_seconds = 0.0
        self.tool_calls = 0
        self.tool_seconds = 0.0
        self.tool_counts: Counter[str] = Counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self._stages[name] += time.perf_counter() - started

    def start_model(self, key: str) -> None:
        with self._lock:
            self.model_calls += 1
            self._model_started[key] = time.perf_counter()

    def end_model(self, key: str) -> None:
        with self._lock:
            started = self._model_started.pop(key, None)
            if started is not None:
                self.model_seconds += time.perf_counter() - started

    def start_tool(self, key: str, name: str) -> None:
        with self._lock:
            self.tool_calls += 1
            self.tool_counts[name] += 1
            self._tool_started[key] = (name, time.perf_counter())

    def end_tool(self, key: str) -> None:
        with self._lock:
            started = self._tool_started.pop(key, None)
            if started is not None:
                self.tool_seconds += time.perf_counter() - started[1]

    def snapshot(self, game: int, backend: str, total_seconds: float) -> Bo3ReviewTiming:
        with self._lock:
            return Bo3ReviewTiming(game=game, backend=backend, total_seconds=total_seconds,
                selection_seconds=self._stages["selection"], evidence_seconds=self._stages["evidence"],
                explanation_seconds=self._stages["explanation"], model_calls=self.model_calls,
                model_seconds=self.model_seconds, tool_calls=self.tool_calls,
                tool_seconds=self.tool_seconds, tool_counts=dict(self.tool_counts))


@contextmanager
def active_review(profile: ReviewProfiler) -> Iterator[None]:
    token = _ACTIVE.set(profile)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def current_review() -> ReviewProfiler | None:
    return _ACTIVE.get()


def langchain_handler(profile: ReviewProfiler) -> Any:
    """Track provider/tool callbacks without importing optional LC at module load."""
    from langchain_core.callbacks import BaseCallbackHandler

    class Handler(BaseCallbackHandler):
        def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID,
                         **kwargs: Any) -> None:
            profile.start_model(str(run_id))

        def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID,
                                **kwargs: Any) -> None:
            profile.start_model(str(run_id))

        def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
            profile.end_model(str(run_id))

        def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            profile.end_model(str(run_id))

        def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: UUID,
                          **kwargs: Any) -> None:
            del input_str
            profile.start_tool(str(run_id), str(serialized.get("name") or "unknown"))

        def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
            del output
            profile.end_tool(str(run_id))

        def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            profile.end_tool(str(run_id))

    return Handler()
