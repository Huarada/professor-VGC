"""Shared fixtures and in-memory test doubles."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from src.adapters.llm.evidence_tools import EvidenceTools
from src.adapters.llm.prompts import FilePromptRepository
from src.domain.models import CalcRequest, ChatMessage, DamageResult, MoveInfo, SpeedComparison
from src.services.ground_truth import GroundTruthAssembler

PROMPTS = FilePromptRepository()

# Status (0-power) moves the fakes below classify as non-damaging — the real
# adapter asks @smogon/calc's own dex instead (see calcEngine.moveInfo).
FAKE_STATUS_MOVES = {
    "Protect", "Detect", "Spiky Shield", "Tailwind", "Trick Room", "Thunder Wave",
    "Will-O-Wisp", "Helping Hand", "Follow Me", "Rage Powder", "Swords Dance",
    "Nasty Plot", "Calm Mind", "Dragon Dance", "Spore", "Sleep Powder", "Wide Guard",
    "Reflect", "Light Screen", "Parting Shot", "Life Dew", "Strength Sap",
}


def fake_move_info(move: str) -> MoveInfo:
    """Deterministic MoveInfo for test doubles."""
    return MoveInfo(
        name=move,
        known=True,
        category="Status" if move in FAKE_STATUS_MOVES else "Physical",
        is_protect=move in {"Protect", "Detect", "Spiky Shield"},
        speed_control={"Tailwind": "tailwind", "Trick Room": "trick_room",
                       "Thunder Wave": "paralysis", "Icy Wind": "speed_drop",
                       "Electroweb": "speed_drop"}.get(move, ""),
        speed_drop_stages=1 if move in {"Icy Wind", "Electroweb"} else 0,
    )

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


def build_evidence(chaos_path: Path, calc_engine: object | None = None) -> GroundTruthAssembler:
    """The shared evidence stage wired to the bundled sample Chaos file."""
    from src.adapters.chaos.chaos_adapter import ChaosAdapter
    from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter

    return GroundTruthAssembler(
        meta_provider=ChaosAdapter(chaos_path),
        calc_engine=calc_engine or FakeCalcEngine(),  # type: ignore[arg-type]
        strategy_provider=ChaosStrategyAdapter(chaos_path),
    )


def build_tools(chaos_path: Path, calc_engine: object | None = None) -> EvidenceTools:
    """The agents' shared tool core wired like ``build_evidence``."""
    from src.adapters.chaos.chaos_adapter import ChaosAdapter
    from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter

    return EvidenceTools(
        calc_engine=calc_engine or FakeCalcEngine(),  # type: ignore[arg-type]
        meta_provider=ChaosAdapter(chaos_path),
        strategy_provider=ChaosStrategyAdapter(chaos_path),
    )


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

    def move_info(self, gen: int, move: str) -> MoveInfo:
        return fake_move_info(move)

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
