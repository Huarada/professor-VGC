"""Independent endpoint oracle, partial certified exclusions and sound resumption."""

import json

from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import (
    DamageObservation,
    HPReading,
    InferenceOptions,
)
from src.domain.models import CalcRequest, PokemonSet
from src.services.bo3_inference.legacy.boundary_search import boundary_bounds
from src.services.bo3_inference.solver import ProjectionBudget
from tests.test_bo3_inference import AnalyticProjection, sheet


def setup(*, ko=False, unavailable=False):
    declared = JsonTeamSheetParser().parse(sheet(known_attack=False))
    declarations = {f"{entry.participant}:slot{entry.slot}": entry for entry in declared.pokemon}
    observation = DamageObservation(id="g1:e1:p2:Incineroar", game=1, event_index=1,
        attacker_id="Alice:slot1", defender_id="Bob:slot1", request=CalcRequest(gen=0,
            attacker=PokemonSet(species="Garchomp"), defender=PokemonSet(species="Incineroar"), move="Dragon Claw"),
        before=HPReading(numerator=110, denominator=110, mode="exact"),
        after=HPReading(numerator=0 if ko else 50, denominator=110, mode="exact"),
        attacker_before=HPReading(numerator=100, denominator=100, mode="exact"))
    port = AnalyticProjection()
    port.unavailable = unavailable
    budget = ProjectionBudget(port, InferenceOptions(max_projections=120000))
    budget.deadline = float("inf")
    return declarations, observation, port, budget


def test_boundary_extrema_equal_the_independent_full_joint_oracle_without_claiming_family_count():
    declarations, observation, port, budget = setup()
    original = json.dumps({k: v.model_dump() for k, v in declarations.items()}, sort_keys=True)
    proof = boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=2000, deadline=float("inf"))
    compatible = [(attack, defense) for attack in range(33) for defense in range(33) if 50 + attack - defense == 60]
    actor = next(s for s in proof.summaries if s.pokemon_id == "Alice:slot1")
    target = next(s for s in proof.summaries if s.pokemon_id == "Bob:slot1")
    assert (actor.stat_points["atk"].minimum, actor.stat_points["atk"].maximum) == (
        min(a for a, _ in compatible), max(a for a, _ in compatible))
    assert (target.stat_points["def"].minimum, target.stat_points["def"].maximum) == (
        min(d for _, d in compatible), max(d for _, d in compatible))
    assert proof.marginal_bounds_complete and proof.coverage == "bounded"
    assert proof.compatible_families is None and not proof.joint_examples
    assert json.dumps({k: v.model_dump() for k, v in declarations.items()}, sort_keys=True) == original


def test_budget_keeps_only_certified_exclusions_and_resumes_to_the_same_exact_marginals():
    declarations, observation, port, budget = setup()
    partial = boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=250, deadline=float("inf"))
    actor = next(s for s in partial.summaries if s.pokemon_id == "Alice:slot1")
    assert not partial.marginal_bounds_complete and partial.coverage == "bounded"
    assert 0 < actor.stat_points["atk"].minimum < 10 <= actor.stat_points["atk"].maximum
    assert budget.factor_limit_reasons[1]
    complete = boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=2000, deadline=float("inf"), previous=partial)
    actor = next(s for s in complete.summaries if s.pokemon_id == "Alice:slot1")
    target = next(s for s in complete.summaries if s.pokemon_id == "Bob:slot1")
    assert complete.marginal_bounds_complete and actor.stat_points["atk"].minimum == 10
    assert target.stat_points["def"].maximum == 22


def test_unsupported_projection_or_elapsed_deadline_never_proves_an_exclusion():
    declarations, observation, port, budget = setup(unavailable=True)
    assert boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=2000, deadline=float("inf")) is None
    assert boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=2000, deadline=0) is None


def test_impossible_damage_is_a_conflict_only_after_all_completions_are_checked():
    declarations, observation, port, budget = setup(ko=True)
    proof = boundary_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
        rules=port.rules(), budget=budget, projection_limit=2000, deadline=float("inf"))
    assert proof.outcome == "conflict" and proof.marginal_bounds_complete
