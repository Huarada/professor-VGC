"""Independent BO3 damage tables may use separate workers without reordering facts."""

from __future__ import annotations

import threading
import time

from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.domain.bo3_inference.models import HPReading, TableSupport
from src.services.bo3_inference.propagation import table_request
from tests.test_hit_table_propagation import table_setup


class SharedCalls:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.calls = 0


class FakeWorker:
    def __init__(self, shared: SharedCalls) -> None:
        self.shared = shared

    def fork(self) -> FakeWorker:
        return FakeWorker(self.shared)

    def request(self, payload: dict[str, object]) -> dict[str, object]:
        with self.shared.lock:
            self.shared.active += 1
            self.shared.maximum = max(self.shared.maximum, self.shared.active)
            self.shared.calls += 1
        time.sleep(0.03)
        with self.shared.lock:
            self.shared.active -= 1
        request = payload["request"]
        assert isinstance(request, dict)
        axes = request["axes"]
        assert isinstance(axes, list)
        return {"ok": True, "result": {"cells": "1" * 33 ** len(axes)}}

    def close(self) -> None:
        pass


def test_candidate_table_batch_uses_independent_workers_and_preserves_cache() -> None:
    declarations, observation, _, _ = table_setup()
    built = table_request(observation, declarations)
    assert built is not None
    _, first = built
    second = first.model_copy(update={"after": HPReading(numerator=59, denominator=100, mode="percent")})
    shared = SharedCalls()
    adapter = SmogonCandidateProjectionAdapter(ipc=FakeWorker(shared))  # type: ignore[arg-type]
    results = adapter.project_tables([first, second, first])
    assert all(isinstance(result, TableSupport) for result in results)
    assert results[0] == results[2]
    assert shared.calls == 2 and shared.maximum == 2
    assert adapter.project_tables([second, first]) == [results[1], results[0]]
    assert shared.calls == 2
    adapter.close()


def test_candidate_table_batch_can_be_limited_to_one_worker() -> None:
    declarations, observation, _, _ = table_setup()
    built = table_request(observation, declarations)
    assert built is not None
    _, first = built
    second = first.model_copy(update={"after": HPReading(numerator=58, denominator=100, mode="percent")})
    shared = SharedCalls()
    adapter = SmogonCandidateProjectionAdapter(ipc=FakeWorker(shared), table_workers=1)  # type: ignore[arg-type]
    assert all(isinstance(result, TableSupport) for result in adapter.project_tables([first, second]))
    assert shared.calls == 2 and shared.maximum == 1
    adapter.close()
