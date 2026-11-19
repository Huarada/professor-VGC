"""BO3 prefetch overlaps selection without changing calculated game evidence."""

from __future__ import annotations

import json
from threading import Event

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.config import Settings
from src.domain.models import AnalysisRequest, SelectionPlan
from src.services.evidence_prefetch import prefetch_during_selection
from tests.conftest import SAMPLE_DIR, build_evidence


def _request(*, bo3: bool) -> AnalysisRequest:
    replay = json.loads((SAMPLE_DIR / "sample_replay.json").read_text(encoding="utf-8"))
    return AnalysisRequest(session_id="prefetch-parity", replay_json=replay,
        question="Explain the damage and decisions", series_context={"game": 1} if bo3 else None)


def test_bo3_prefetch_preserves_the_exact_typed_evidence() -> None:
    assembler = build_evidence(SAMPLE_DIR / "gen9championsvgc2026regmb.json")
    request = _request(bo3=True)
    state = assembler.prepare(ShowdownReplayParser(), request)
    selection = SelectionPlan(focus_species=[state.sides[0].team[0].species])
    sequential = assembler.assemble(request=request, game_state=state, selection=selection, history=[])
    prefetched = assembler.prefetch(request, state)
    concurrent = assembler.assemble(request=request, game_state=state, selection=selection,
                                    history=[], prefetched=prefetched)
    assert concurrent.model_dump(mode="json") == sequential.model_dump(mode="json")


def test_bo3_prefetch_starts_during_selection_and_bo1_stays_sequential(monkeypatch) -> None:
    assembler = build_evidence(SAMPLE_DIR / "gen9championsvgc2026regmb.json")
    request = _request(bo3=True)
    state = assembler.prepare(ShowdownReplayParser(), request)
    started, selection_finished = Event(), Event()
    original = assembler.prefetch

    def observed_prefetch(current_request, game_state):
        started.set()
        assert selection_finished.wait(timeout=5)
        return original(current_request, game_state)

    monkeypatch.setattr(assembler, "prefetch", observed_prefetch)
    with prefetch_during_selection(assembler, request, state) as pending:
        assert pending is not None and started.wait(timeout=5)
        selection_finished.set()
        assert pending.result(timeout=5).species
    with prefetch_during_selection(assembler, _request(bo3=False), state) as pending:
        assert pending is None
    assembler.prefetch_enabled = False
    with prefetch_during_selection(assembler, request, state) as pending:
        assert pending is None


def test_prefetch_with_an_unexpected_focus_recomputes_full_context() -> None:
    assembler = build_evidence(SAMPLE_DIR / "gen9championsvgc2026regmb.json")
    request = _request(bo3=True)
    state = assembler.prepare(ShowdownReplayParser(), request)
    selection = SelectionPlan(focus_species=["UnexpectedSpecies"])
    prefetched = assembler.prefetch(request, state)
    sequential = assembler.assemble(request=request, game_state=state, selection=selection, history=[])
    guarded = assembler.assemble(request=request, game_state=state, selection=selection,
                                 history=[], prefetched=prefetched)
    assert guarded.model_dump(mode="json") == sequential.model_dump(mode="json")


def test_bo3_prefetch_can_be_disabled_by_configuration(monkeypatch) -> None:
    monkeypatch.setenv("PROFESSORVGC_BO3_PREFETCH_CONTEXT", "false")
    assert Settings().bo3_prefetch_context is False
