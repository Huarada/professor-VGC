"""All projection paths must leave resources for later games, including fallback."""

import pytest

from src.domain.bo3_inference.models import (
    HPReading,
    InferenceOptions,
    ProjectionRequest,
)
from src.domain.models import CalcRequest, PokemonSet
from src.services.bo3_inference.solver import ProjectionBudget, SearchLimit
from tests.test_bo3_inference import AnalyticProjection


def request(points):
    return ProjectionRequest(calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp"),
        defender=PokemonSet(species="Incineroar"), move="Dragon Claw"), attacker_points={"atk": points},
        before=HPReading(numerator=100, denominator=100, mode="exact"),
        attacker_before=HPReading(numerator=100, denominator=100, mode="exact"))


def test_fallback_projection_cannot_consume_the_next_games_projection_reservation():
    port = AnalyticProjection()
    budget = ProjectionBudget(port, InferenceOptions(max_projections=6))
    budget.game_count = 3
    for game in (1, 2, 3):
        budget.start_game(game)
        budget.project([request((game - 1) * 2), request((game - 1) * 2 + 1)])
        if game < 3:
            with pytest.raises(SearchLimit, match="Per-game"):
                budget.project([request(game * 2)])
        assert budget.projections == game * 2
    assert len(port.requests) == 6


def test_expired_game_deadline_does_not_spend_the_rest_of_the_series(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("src.services.bo3_inference.solver.time.monotonic", lambda: clock[0])
    budget = ProjectionBudget(AnalyticProjection(), InferenceOptions(timeout_seconds=120))
    budget.game_count = 3
    budget.start_game(1)
    clock[0] = 40.0
    with pytest.raises(SearchLimit, match="Per-game"):
        budget.project([request(1)])
    budget.start_game(2)
    budget.project([request(2)])
    clock[0] = 80.0
    with pytest.raises(SearchLimit, match="Per-game"):
        budget.project([request(3)])
    budget.start_game(3)
    budget.project([request(3)])
    assert budget.projections == 2
    clock[0] = 120.0
    with pytest.raises(SearchLimit, match="Projection/time"):
        budget.project([request(4)])
