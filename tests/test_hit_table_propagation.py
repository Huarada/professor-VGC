"""Per-hit tables + arc consistency: sound outer bounds against an independent oracle (ADR-068)."""

import itertools
import random

from src.domain.bo3_inference.constraints import displays
from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.models import (
    HPReading,
    InferenceOptions,
    PointRange,
    TableSupport,
)
from src.domain.bo3_inference.session import retain_known_bounds
from src.services.bo3_inference.propagation import (
    SIDE,
    coordinate_mask,
    propagate,
    propagated_bounds,
)
from src.services.bo3_inference.solver import ProjectionBudget, solve
from tests.test_bo3_inference import AnalyticProjection
from tests.test_boundary_search import setup


class TableProjection(AnalyticProjection):
    """Builds every cell through the batch path, so both paths share one semantics."""

    def __init__(self):
        super().__init__()
        self.tables = 0

    def project_table(self, request):
        self.tables += 1
        cells = []
        for combo in itertools.product(range(SIDE), repeat=len(request.axes)):
            points = {"attacker": dict(request.base.attacker_points), "defender": dict(request.base.defender_points)}
            for axis, value in zip(request.axes, combo):
                points[axis.side][axis.stat] = value
            if any(sum(side.values()) > 66 for side in points.values()):
                cells.append("0")
                continue
            support = self.project_batch([request.base.model_copy(update={
                "attacker_points": points["attacker"], "defender_points": points["defender"]})])[0]
            agrees = any(displays(max(0, witness.hp_before - witness.damage), support.maximum_hp, request.after)
                         for witness in support.witnesses)
            cells.append("?" if not support.supported else "1" if agrees else "0")
        return TableSupport(cells="".join(cells))


def table_setup(**options):
    declarations, observation, _, _ = setup(**options)
    port = TableProjection()
    port.unavailable = options.get("unavailable", False)
    budget = ProjectionBudget(port, InferenceOptions(max_projections=120000))
    budget.deadline = float("inf")
    return declarations, observation, port, budget


def bounds(checkpoint, pokemon_id, stat):
    summary = next(entry for entry in checkpoint.summaries if entry.pokemon_id == pokemon_id)
    return summary.stat_points[stat].minimum, summary.stat_points[stat].maximum


def test_table_bounds_equal_the_independent_full_joint_oracle():
    declarations, observation, port, budget = table_setup()
    proof = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                              rules=port.rules(), budget=budget)
    compatible = [(attack, defense) for attack in range(33) for defense in range(33) if 50 + attack - defense == 60]
    assert bounds(proof, "Alice:slot1", "atk") == (min(a for a, _ in compatible), max(a for a, _ in compatible))
    assert bounds(proof, "Bob:slot1", "def") == (min(d for _, d in compatible), max(d for _, d in compatible))
    assert proof.outcome == "ambiguous" and proof.observation_ids == (observation.id,)
    assert proof.compatible_families is None and not proof.joint_examples


def test_unsupported_cells_never_exclude_a_value():
    declarations, observation, port, budget = table_setup(unavailable=True)
    proof = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                              rules=port.rules(), budget=budget)
    assert proof.outcome == "ambiguous"
    assert bounds(proof, "Alice:slot1", "atk") == (0, 32) and bounds(proof, "Bob:slot1", "def") == (0, 32)


def test_impossible_damage_is_a_conflict():
    declarations, observation, port, budget = table_setup(ko=True)
    proof = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                              rules=port.rules(), budget=budget)
    assert proof.outcome == "conflict"


def test_single_hit_dependency_is_reported_and_redundant_evidence_clears_it():
    declarations, observation, port, budget = table_setup()
    alone = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                              rules=port.rules(), budget=budget)
    attacker = next(entry for entry in alone.summaries if entry.pokemon_id == "Alice:slot1")
    assert attacker.single_observation_bounds == {"atk": (observation.id,)}
    twin = observation.model_copy(update={"id": "g1:e2:p2:Incineroar", "event_index": 2})
    both = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation, twin],
                             rules=port.rules(), budget=budget)
    assert bounds(both, "Alice:slot1", "atk") == bounds(alone, "Alice:slot1", "atk")
    assert all(not entry.single_observation_bounds for entry in both.summaries)
    assert port.tables == 1  # identical requests reuse the series-local table


def test_propagation_keeps_interior_holes_and_applies_the_66_point_budget():
    declarations, _, port, _ = table_setup()
    rules = port.rules()
    holes = sum(1 << value for value in (2, 5, 9))
    alice_atk = ("Alice:slot1", "atk")
    domains = propagate([((alice_atk,), holes)], declarations, rules)
    assert domains == {alice_atk: holes}
    # Bob already declares 10 HP points: def 32 leaves at most 24 for a second free stat.
    budget_case = {**declarations, "Bob:slot1": declarations["Bob:slot1"].model_copy(update={"stat_points": {"hp": 10}})}
    full = (1 << 33) - 1
    domains = propagate([((("Bob:slot1", "def"),), 1 << 32), ((("Bob:slot1", "atk"),), full)], budget_case, rules)
    assert domains[("Bob:slot1", "atk")] == (1 << 25) - 1


def test_coordinate_masks_follow_the_worker_axis_order():
    for _ in range(50):
        values = [random.randrange(SIDE) for _ in range(3)]
        index = values[0] * SIDE * SIDE + values[1] * SIDE + values[2]
        assert all(coordinate_mask(3, position, value) >> index & 1 for position, value in enumerate(values))
        assert not coordinate_mask(3, 0, (values[0] + 1) % SIDE) >> index & 1


def test_solve_uses_tables_and_legacy_remains_selectable():
    declarations, observation, port, budget = table_setup()
    result = solve(game=1, phase="after_game", declarations=declarations, observations=[observation],
                   unsupported=(), rules=port.rules(), budget=budget, assignment_limit=10)
    assert port.tables == 1 and observation.id in result.local_constraints
    assert bounds(result, "Alice:slot1", "atk") == (10, 32)
    legacy = TableProjection()
    legacy_budget = ProjectionBudget(legacy, InferenceOptions(max_projections=120000, search="legacy"))
    legacy_budget.deadline = float("inf")
    solve(game=1, phase="after_game", declarations=declarations, observations=[observation],
          unsupported=(), rules=legacy.rules(), budget=legacy_budget, assignment_limit=10)
    assert legacy.tables == 0


def test_ports_without_tables_fall_back_to_the_legacy_search():
    declarations, observation, port, budget = setup()
    assert propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                             rules=port.rules(), budget=budget) is None
    solve(game=1, phase="after_game", declarations=declarations, observations=[observation],
          unsupported=(), rules=port.rules(), budget=budget, assignment_limit=10)
    assert port.requests  # the legacy path projected through the batch port


def test_retained_bounds_keep_sound_exclusions_inside_the_final_range():
    declarations, observation, port, budget = table_setup()
    proof = propagated_bounds(game=1, phase="after_game", declarations=declarations, observations=[observation],
                              rules=port.rules(), budget=budget)
    old_summaries = tuple(entry.model_copy(update={"excluded_points": {"atk": (12, 31)}})
                          if entry.pokemon_id == "Alice:slot1" else entry for entry in proof.summaries)
    current_summaries = tuple(entry.model_copy(update={
        "stat_points": {**entry.stat_points, "atk": PointRange(minimum=12, maximum=32)},
        "excluded_points": {"atk": (20,)}, "single_observation_bounds": {}})
        if entry.pokemon_id == "Alice:slot1" else entry for entry in proof.summaries)
    merged = retain_known_bounds(proof.model_copy(update={"summaries": current_summaries}),
                                 proof.model_copy(update={"summaries": old_summaries}))
    attacker = next(entry for entry in merged.summaries if entry.pokemon_id == "Alice:slot1")
    # 12 is now the minimum itself, so only interior values remain listed.
    assert attacker.excluded_points == {"atk": (20, 31)}
    assert attacker.single_observation_bounds == {"atk": (observation.id,)}


def test_color_qualified_attacker_hp_makes_its_hp_points_a_variable():
    # 50%y is exactly half HP, which needs an even maximum: zero attacker HP points
    # would otherwise reject every spread for a reason unrelated to damage.
    declarations, observation, _, _ = setup()
    plain = observation.model_copy(update={"attacker_before": HPReading(numerator=50, denominator=100, mode="percent")})
    yellow = plain.model_copy(update={"attacker_before": HPReading(
        numerator=50, denominator=100, mode="percent", color="y")})
    assert "hp" not in relevant_axes([plain])["Alice:slot1"]
    assert "hp" in relevant_axes([yellow])["Alice:slot1"]
