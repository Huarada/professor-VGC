"""Shared fixtures and in-memory test doubles."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from src.domain.models import CalcRequest, ChatMessage, DamageResult, SpeedComparison

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


class FakeCalcEngine:
    """Deterministic stand-in for the Node @smogon/calc adapter."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        base = 40 + len(request.move)
        return DamageResult(
            attacker=request.attacker.species,
            defender=request.defender.species,
            move=request.move,
            damage_rolls=[base, base + 5],
            min_percent=float(base),
            max_percent=float(base + 5),
            ko_chance_text="2HKO",
            is_ko_guaranteed=False,
            description=f"{request.attacker.species} {request.move} vs {request.defender.species}",
        )

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        return SpeedComparison(
            faster=request.attacker.species,
            slower=request.defender.species,
            faster_speed=120,
            slower_speed=60,
        )

    def forme_resolves(self, gen: int, species: str) -> bool:
        return False

    def close(self) -> None:  # pragma: no cover
        pass


class FakeLLM:
    """Records prompts and returns scripted responses."""

    name = "fake"

    def __init__(self, selection_json: str, explanation: str) -> None:
        self._selection_json = selection_json
        self._explanation = explanation
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({"system": system, "json_mode": json_mode, "messages": list(messages)})
        return self._selection_json if json_mode else self._explanation


@pytest.fixture
def sample_chaos_path() -> Path:
    return SAMPLE_DIR / "gen9championsvgc2026regmb.json"


@pytest.fixture
def sample_replay_path() -> Path:
    return SAMPLE_DIR / "sample_replay.json"


@pytest.fixture
def fake_calc() -> FakeCalcEngine:
    return FakeCalcEngine()


@pytest.fixture
def patch_firestore_repo(monkeypatch):
    """Redirects Container.chaos_repository()'s FirestoreChaosRepository
    construction onto a fake Firestore client (test_firestore_chaos_
    repository.py's own _FakeFirestoreClient/_seed_store — the exact same
    fixture data as test_chaos_repository.py's local-file fixture, so
    Container-level tests exercise realistic species/tier data without any
    network/credentials). Since the app's own Chaos backend is Firestore-
    only now (no local-file option — see config.py), this is the standard
    way any test that builds a real Container and reaches chaos()/
    _chaos_strategy()/strategy() stays hermetic.

    Local import (not a top-of-file one): keeps conftest.py's own import
    order independent of the test-module import order pytest happens to
    use.
    """
    from src.adapters.chaos.firestore_chaos_repository import FirestoreChaosRepository
    from tests.test_firestore_chaos_repository import _FakeFirestoreClient, _seed_store

    def _fake_constructor(
        project_id,
        *,
        database_id="(default)",
        collection="chaos_tiers",
        credentials_path=None,
        grpc_ca_bundle_path=None,
        reg_fallback_depth=3,
    ):
        return FirestoreChaosRepository(
            project_id or "test-project",
            client=_FakeFirestoreClient(_seed_store()),
            reg_fallback_depth=reg_fallback_depth,
        )

    monkeypatch.setattr("src.services.container.FirestoreChaosRepository", _fake_constructor)
