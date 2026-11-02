"""Independent planted damage constraints and conservative interval accounting."""

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import DamageWitness, InferenceOptions, PointRange
from src.domain.bo3_inference.range_reduction import compare_ranges
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference import AnalyticProjection, log, sheet, summary


def test_reduction_counts_integer_candidates_not_interval_width_or_confidence():
    change = compare_ranges(PointRange(minimum=0, maximum=32), PointRange(minimum=10, maximum=20))
    assert change.removed_integer_values == 22
    assert change.reduction_percent == 66.7
    assert compare_ranges(PointRange(minimum=7, maximum=7), PointRange(minimum=7, maximum=7)).reduction_percent == 0
    assert compare_ranges(PointRange(minimum=2, maximum=12), PointRange(minimum=0, maximum=10)) is None


def test_declared_budget_limits_are_baseline_information_not_damage_reduction():
    from src.domain.bo3_inference.models import DeclaredSet
    from src.domain.bo3_inference.session import summarize

    declaration = DeclaredSet(participant="Alice", slot=1, species="Garchomp", nature="Hardy",
        stat_points={"hp": 32, "atk": 32, "def": 2})
    current = summarize("Alice:slot1", declaration, AnalyticProjection().rules())
    assert current.baseline_stat_points["spe"] == PointRange(minimum=0, maximum=0)
    for stat, initial in current.baseline_stat_points.items():
        assert compare_ranges(initial, current.stat_points[stat]).reduction_percent == 0


class BoostedOracle(AnalyticProjection):
    def project_batch(self, requests):
        return [result.model_copy(update={"witnesses": tuple(
            DamageWitness(hp_before=w.hp_before, damage=w.damage + 30 * request.calc.attacker.boosts.get("atk", 0))
            for w in result.witnesses)})
            for request, result in zip(requests, super().project_batch(requests))]


def test_only_registered_cross_game_damage_narrows_hidden_hp(monkeypatch):
    from src.services.bo3_inference.solver import ProjectionBudget

    class WorkLimitedBudget(ProjectionBudget):
        def __init__(self, port, options):
            super().__init__(port, options)
            self.deadline = float("inf")

    monkeypatch.setattr("src.services.bo3_inference.coordinator.ProjectionBudget", WorkLimitedBudget)
    games = [log(hp=100, damage=45), log(hp=100, damage=72, winner="Bob", turn=2).replace(
        "|turn|2", "|-boost|p1a: Garchomp|atk|1\n|turn|2")]
    # Independent exhaustive oracle: ceil-percent damage at 100->55 and 100->28.
    compatible_hp = set()
    for hp in range(33):
        maximum = 100 + hp
        for attack in range(33):
            for defense in range(33):
                damage = 50 + attack - defense
                matches = []
                for observed, extra in [(55, 0), (28, 30)]:
                    matches.append(any((remaining * 100 + maximum - 1) // maximum == observed
                        for before in range(1, maximum + 1)
                        if (before * 100 + maximum - 1) // maximum == 100
                        and (remaining := before - damage - extra) > 0))
                if all(matches):
                    compatible_hp.add(hp)
    assert (min(compatible_hp), max(compatible_hp)) == (6, 18)
    for registered in [True, False]:
        source = sheet(registered=registered, known_attack=False)
        del source["pokemon"][1]["stat_points"]["hp"]
        request = Bo3AnalysisRequest(session_id="cross-damage", games=games, teamsheet=source,
            inference_options=InferenceOptions(hp_mode="percent", max_assignments=12000,
                max_projections=120000, timeout_seconds=120))
        evidence, states = prepare_series(ShowdownReplayParser(), request)
        result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=BoostedOracle()).analyze(request, evidence, states)
        assert result.evidence_retrievals
        if registered:
            assert len({entry.scope for entry in result.evidence_retrievals}) == 1
            assert any({identifier.split(":")[0] for identifier in entry.observation_ids} == {"g1", "g2"}
                       for entry in result.evidence_retrievals)
        else:
            assert len({entry.scope for entry in result.evidence_retrievals}) == 2
            assert all(len({identifier.split(":")[0] for identifier in entry.observation_ids}) <= 1
                       for entry in result.evidence_retrievals)
        first = summary(result.checkpoints[1], "Bob:slot1")
        assert (first.stat_points["hp"].minimum, first.stat_points["hp"].maximum) == (0, 32)
        final = summary(result.checkpoints[-1], "Bob:slot1")
        bound = final.stat_points["hp"]
        assert (bound.minimum, bound.maximum) == ((6, 18) if registered else (0, 32))
        change = compare_ranges(final.baseline_stat_points["hp"], bound)
        assert change.reduction_percent == (60.6 if registered else 0)
        assert bound.minimum <= 10 <= bound.maximum  # planted truth retained
