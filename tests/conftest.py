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
        self.calls.append({"system": system, "json_mode": json_mode})
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
