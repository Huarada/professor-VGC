"""Independent inverse constraints compose without searching unrelated joint products."""

from copy import deepcopy

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.components import observation_components
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import observations
from src.services.bo3_inference.solver import ProjectionBudget, enumerate_constraints
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference import AnalyticProjection, log, run, sheet, summary


def two_pairs(*, known_attack=True, first_damage=62):
    declarations = sheet(known_attack=known_attack)
    extra = deepcopy(declarations["pokemon"])
    extra[0].update(slot=2, species="Talonflame")
    extra[1].update(slot=2, species="Dragonite")
    declarations["pokemon"].extend(extra)
    source = log(damage=first_damage).replace("|turn|1", "\n".join([
        "|poke|p1|Talonflame, L50|", "|poke|p2|Dragonite, L50|",
        "|switch|p1b: Talonflame|Talonflame, L50|100/100",
        "|switch|p2b: Dragonite|Dragonite, L50|110/110", "|turn|1",
    ])).replace("|win|Alice", "\n".join([
        "|turn|2", "|move|p1b: Talonflame|Dragon Claw|p2b: Dragonite",
        f"|-damage|p2b: Dragonite|{110-first_damage}/110", "|win|Alice",
    ]))
    return source, declarations


def test_two_independent_defenders_require_66_assignments_instead_of_1089():
    source, declarations = two_pairs()
    optimized = run(source, teamsheet=declarations, max_assignments=70).checkpoints[-1]
    request = Bo3AnalysisRequest(session_id="baseline", games=[source], teamsheet=declarations,
                                 inference_options=InferenceOptions(hp_mode="exact", max_assignments=2000))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    parsed = JsonTeamSheetParser().parse(declarations)
    initial, maps = identities(evidence, states, parsed)
    current = visible_declarations(initial, parsed, 1, "public")
    port = AnalyticProjection()
    found, skipped = observations(states[0], 1, current, maps[0], port, "exact")
    baseline = enumerate_constraints(game=1, phase="after_game", declarations=current,
        observations=found, unsupported=skipped, rules=port.rules(), budget=ProjectionBudget(port, request.inference_options))
    assert optimized.search_components == 2
    assert optimized.evaluated_assignments == 66
    assert baseline.evaluated_assignments == 1089
    assert optimized.summaries == baseline.summaries
    assert optimized.compatible_families == baseline.compatible_families == 1
    assert optimized.outcome == baseline.outcome == "identified_within_model"


def test_unknown_attack_and_defense_remain_correlated_inside_each_component():
    source, declarations = two_pairs(known_attack=False, first_damage=60)
    result = run(source, teamsheet=declarations, max_assignments=2200)
    checkpoint = result.checkpoints[-1]
    assert checkpoint.coverage == "exhaustive" and checkpoint.outcome == "ambiguous"
    assert checkpoint.evaluated_assignments == 2178  # 1089 + 1089, not 1089 squared.
    assert checkpoint.compatible_families == 23 * 23
    for example in checkpoint.joint_examples:
        for slot in (1, 2):
            assert example[f"Alice:slot{slot}"].points["atk"] - example[f"Bob:slot{slot}"].points["def"] == 10


def test_checkpoint_assignment_budget_is_shared_between_components():
    source, declarations = two_pairs()
    checkpoint = run(source, teamsheet=declarations, max_assignments=40).checkpoints[-1]
    assert checkpoint.coverage == "bounded"
    assert checkpoint.outcome != "identified_within_model"
    assert checkpoint.compatible_families is None
    assert checkpoint.evaluated_assignments == 40
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].minimum == 20
    assert summary(checkpoint, "Bob:slot2").stat_points["def"].maximum == 20
    assert checkpoint.local_constraints  # Independent exhaustive proofs can still narrow a bounded search.


def test_shared_combatants_cannot_be_split_even_when_their_roles_reverse():
    source, declarations = two_pairs()
    request = Bo3AnalysisRequest(session_id="graph", games=[source])
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    parsed = JsonTeamSheetParser().parse(declarations)
    initial, maps = identities(evidence, states, parsed)
    current = visible_declarations(initial, parsed, 1, "public")
    found, _ = observations(states[0], 1, current, maps[0], AnalyticProjection(), "exact")
    assert len(observation_components(found)) == 2
    bridge = found[0].model_copy(update={"attacker_id": "Bob:slot2", "defender_id": "Alice:slot1"})
    assert len(observation_components([*found, bridge])) == 1


def test_a_proven_impossible_component_makes_the_joint_space_empty():
    source, declarations = two_pairs(first_damage=20)
    checkpoint = run(source, teamsheet=declarations, max_assignments=70).checkpoints[-1]
    assert checkpoint.outcome == "conflict"
    assert checkpoint.compatible_families == 0
    assert not checkpoint.joint_examples


def test_engine_failure_in_one_component_does_not_disprove_profiles_or_erase_other_bounds():
    class PartiallyUnavailable(AnalyticProjection):
        def project_batch(self, requests):
            results = super().project_batch(requests)
            return [support.model_copy(update={"supported": False, "reason": "Test worker failure"})
                    if request.calc.defender.species == "Dragonite" else support
                    for request, support in zip(requests, results)]

    source, declarations = two_pairs()
    checkpoint = run(source, teamsheet=declarations, port=PartiallyUnavailable(), max_assignments=70).checkpoints[-1]
    assert checkpoint.coverage == "bounded" and checkpoint.outcome == "ambiguous"
    assert checkpoint.compatible_families is None and not checkpoint.joint_examples
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].minimum == 20
    assert summary(checkpoint, "Bob:slot2").stat_points["def"].maximum == 32
