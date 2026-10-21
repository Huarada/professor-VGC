"""Inverse inference: planted sets, chronology, joint constraints and honest budgets."""

from __future__ import annotations

import json

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.candidate_space import profiles
from src.domain.bo3_inference.models import (
    DamageSupport,
    DamageWitness,
    DeclaredSet,
    InferenceOptions,
    MoveSupport,
    NatureSpec,
    RuleProfile,
)
from src.domain.exceptions import (
    CalcEngineError,
    SeriesValidationError,
    UnsupportedRulesError,
)
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import prepare_series


class AnalyticProjection:
    """A deterministic independent constraint problem: damage = 50 + Atk SP - Def SP."""

    def __init__(self):
        self.requests = []
        self.unavailable = False

    def rules(self):
        return RuleProfile(version="test-v1", engine_version="fake", natures=(
            NatureSpec(name="Hardy", plus="atk", minus="atk"),
            NatureSpec(name="Docile", plus="def", minus="def"),
            NatureSpec(name="Impish", plus="def", minus="spa"),
        ))

    def move_support(self, move):
        return MoveSupport(supported=move != "Protect", category="Physical")

    def project_batch(self, requests):
        self.requests.extend(requests)
        result = []
        for request in requests:
            if self.unavailable:
                result.append(DamageSupport(supported=False, reason="Engine unavailable for this profile"))
                continue
            attacker = request.attacker_points
            defender = request.defender_points
            maximum = 100 + defender.get("hp", 0)
            bonus = 5 if request.defender_nature == "Impish" else 0
            damage = 50 + attacker.get("atk", 0) - defender.get("def", 0) - bonus
            before = request.before
            if before.mode == "exact":
                values = [before.numerator] if before.denominator == maximum else []
            else:
                values = [hp for hp in range(1, maximum + 1)
                          if (hp * before.denominator + maximum - 1) // maximum == before.numerator]
            result.append(DamageSupport(supported=True, maximum_hp=maximum,
                                        witnesses=tuple(DamageWitness(hp_before=hp, damage=damage) for hp in values)))
        return result


def log(*, damage=62, winner="Alice", swapped=False, tag="", hp=110, turn=1):
    names = ("Bob", "Alice") if swapped else ("Alice", "Bob")
    sides = {name: f"p{i}" for i, name in enumerate(names, 1)}
    lines = ["|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)"]
    for name in names:
        side = sides[name]
        species, maximum = ("Garchomp", 100) if name == "Alice" else ("Incineroar", hp)
        lines += [f"|player|{side}|{name}|", f"|poke|{side}|{species}, L50|",
                  f"|switch|{side}a: {species}|{species}, L50|{maximum}/{maximum}"]
    lines += [f"|turn|{turn}", f"|move|{sides['Alice']}a: Garchomp|Dragon Claw|{sides['Bob']}a: Incineroar"]
    if tag:
        lines.append(f"|-{tag}|{sides['Bob']}a: Incineroar")
    lines += [f"|-damage|{sides['Bob']}a: Incineroar|{hp - damage}/{hp}", f"|win|{winner}"]
    return "\n".join(lines)


def sheet(*, registered=True, known_attack=True, known_defense=False, nature="Hardy"):
    attacker = {"participant": "Alice", "slot": 1, "species": "Garchomp", "item": "",
                "ability": "Rough Skin", "nature": "Hardy",
                "stat_points": {"hp": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}}
    if known_attack:
        attacker["stat_points"]["atk"] = 32
    defender = {"participant": "Bob", "slot": 1, "species": "Incineroar", "item": "",
                "ability": "Intimidate", "nature": nature,
                "stat_points": {"hp": 10, "atk": 0, "spa": 0, "spd": 0, "spe": 0}}
    if known_defense:
        defender["stat_points"]["def"] = 20
    return {"registered_team": registered, "pokemon": [attacker, defender]}


def run(*logs, teamsheet=None, port=None, **options):
    request = Bo3AnalysisRequest(session_id="infer", games=list(logs), teamsheet=teamsheet,
                                 inference_options=InferenceOptions(hp_mode="exact", **options))
    parser = ShowdownReplayParser()
    evidence, states = prepare_series(parser, request)
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=port or AnalyticProjection())
    result = coordinator.analyze(request, evidence, states)
    assert result is not None
    return result


def summary(checkpoint, pokemon_id):
    return next(entry for entry in checkpoint.summaries if entry.pokemon_id == pokemon_id)


def test_planted_unknown_defense_is_identified_from_damage_without_changing_input():
    source = sheet()
    before = json.dumps(source, sort_keys=True)
    result = run(log(), teamsheet=source)
    checkpoint = result.checkpoints[-1]
    assert checkpoint.coverage == "exhaustive"
    assert checkpoint.outcome == "identified_within_model"
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].minimum == 20
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].maximum == 20
    assert json.dumps(source, sort_keys=True) == before
    assert result.iv_policy == "fixed_at_31_not_inferred"
    assert result.conditioned_projections[0].minimum_damage == 62
    assert result.conditioned_projections[0].coverage == "exhaustive"


def test_unknown_attacker_and_defender_retain_correlated_joint_solutions():
    result = run(log(damage=60), teamsheet=sheet(known_attack=False))
    checkpoint = result.checkpoints[-1]
    assert checkpoint.coverage == "exhaustive" and checkpoint.outcome == "ambiguous"
    assert checkpoint.compatible_families == 23
    for example in checkpoint.joint_examples:
        assert example["Alice:slot1"].points["atk"] - example["Bob:slot1"].points["def"] == 10
    assert summary(checkpoint, "Alice:slot1").stat_points["atk"].minimum == 10
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].maximum == 22


def test_native_points_are_preserved_until_engine_boundary():
    port = AnalyticProjection()
    run(log(), teamsheet=sheet(), port=port)
    assert port.requests
    assert {request.attacker_points["atk"] for request in port.requests} == {32}
    assert all(request.calc.attacker.evs is None for request in port.requests)
    assert all(request.calc.gen == 0 for request in port.requests)


def test_registered_series_carries_constraints_without_p1_p2_confusion():
    result = run(log(), log(swapped=True, turn=2), teamsheet=sheet())
    first, before_second = result.checkpoints[1:3]
    assert before_second.phase == "before_game"
    assert before_second.observation_ids == first.observation_ids
    assert summary(before_second, "Bob:slot1").stat_points["def"].minimum == 20


def test_unverified_continuity_resets_constraints_and_conflicts_stay_game_local():
    result = run(log(), log(damage=61, turn=2), teamsheet=sheet(registered=False))
    before_second = result.checkpoints[2]
    assert before_second.observation_ids == ()
    assert summary(before_second, "Bob:slot1").stat_points["def"].minimum == 0
    assert summary(result.checkpoints[-1], "Bob:slot1").stat_points["def"].minimum == 21


def test_contradictory_hits_report_conflict_without_forgetting_last_valid_range():
    result = run(log(), log(damage=61, turn=2), teamsheet=sheet())
    checkpoint = result.checkpoints[-1]
    assert checkpoint.outcome == "conflict"
    assert len(checkpoint.observation_ids) == 2
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].minimum == 20


@pytest.mark.parametrize("limits", [{"max_assignments": 1}, {"max_projections": 1}, {"timeout_seconds": 0.000001}])
def test_budget_exhaustion_never_claims_exact_spreads_or_false_conflict(limits, monkeypatch):
    if "timeout_seconds" in limits:
        from types import SimpleNamespace
        ticks = iter(range(10000))
        monkeypatch.setattr("src.services.bo3_inference.solver.time", SimpleNamespace(monotonic=lambda: next(ticks)))
    result = run(log(), teamsheet=sheet(), **limits)
    checkpoint = result.checkpoints[-1]
    assert checkpoint.coverage == "bounded"
    assert checkpoint.outcome not in {"identified_within_model", "conflict"}
    bound = summary(checkpoint, "Bob:slot1").stat_points["def"]
    assert (bound.minimum, bound.maximum) == (0, 32)


def test_projection_failures_remain_unresolved_instead_of_eliminating_profiles():
    port = AnalyticProjection()
    port.unavailable = True
    checkpoint = run(log(), teamsheet=sheet(), port=port).checkpoints[-1]
    assert checkpoint.coverage == "bounded"
    assert checkpoint.outcome != "conflict"
    assert checkpoint.unsupported


def test_no_teamsheet_never_turns_chaos_guesses_into_facts():
    checkpoint = run(log()).checkpoints[-1]
    assert checkpoint.outcome == "not_informative" and checkpoint.coverage == "unsupported"
    assert any("unknown" in reason for reason in checkpoint.unsupported)


def test_critical_hit_is_excluded_but_logged_with_its_reason():
    checkpoint = run(log(tag="crit"), teamsheet=sheet()).checkpoints[-1]
    assert not checkpoint.observation_ids
    assert any("Critical" in reason for reason in checkpoint.unsupported)


def test_future_private_declarations_do_not_change_earlier_checkpoints():
    source = sheet()
    source["pokemon"][1]["available_from_game"] = 2
    result = run(log(), log(turn=2), teamsheet=source)
    assert result.checkpoints[1].outcome == "not_informative"
    assert not result.checkpoints[1].observation_ids
    assert summary(result.checkpoints[-1], "Bob:slot1").stat_points["def"].minimum == 20


def test_private_spread_is_not_visible_to_public_or_opponent():
    source = sheet(known_attack=False)
    source["pokemon"].append({"participant": "Alice", "slot": 1, "species": "Garchomp",
                              "stat_points": {"atk": 32}, "visibility": "owner_private"})
    public = run(log(), teamsheet=source)
    owner = run(log(), teamsheet=source, perspective="Alice")
    opponent = run(log(), teamsheet=source, perspective="Bob")
    assert summary(public.checkpoints[0], "Alice:slot1").stat_points["atk"].minimum == 0
    assert summary(opponent.checkpoints[0], "Alice:slot1").stat_points["atk"].minimum == 0
    assert summary(owner.checkpoints[0], "Alice:slot1").stat_points["atk"].minimum == 32
    assert owner.perspective == "Alice"


def test_equivalent_natures_are_not_arbitrarily_reduced_to_one():
    source = sheet()
    source["pokemon"][1].pop("nature")
    result = run(log(), teamsheet=source)
    checkpoint = result.checkpoints[-1]
    assert checkpoint.outcome == "ambiguous"
    assert {"Hardy", "Docile"} <= set(summary(checkpoint, "Bob:slot1").natures)


@pytest.mark.parametrize("patch", [
    {"stat_points": {"atk": 33}}, {"stat_points": {"atk": 32, "hp": 32, "def": 3}},
    {"stat_points": {"atk": 1.5}}, {"stat_points": {"atk": True}}, {"ivs": {"spe": 0}},
    {"evs": {"atk": 252}}, {"nature": "Invented"},
])
def test_invalid_units_spreads_ivs_and_natures_are_refused(patch):
    entry = {"participant": "Alice", "slot": 1, "species": "Garchomp", **patch}
    with pytest.raises(SeriesValidationError):
        JsonTeamSheetParser().parse({"pokemon": [entry]})


def test_foreign_teamsheet_mechanics_are_refused():
    with pytest.raises(UnsupportedRulesError):
        JsonTeamSheetParser().parse({"pokemon": [{"participant": "Alice", "slot": 1,
                                                  "species": "Garchomp", "teraType": "Steel"}]})


def test_unknown_participant_or_species_mapping_is_not_guessed():
    for field, value in (("participant", "Carol"), ("species", "Pikachu")):
        source = sheet()
        source["pokemon"][0][field] = value
        with pytest.raises(SeriesValidationError):
            run(log(), teamsheet=source)


def test_rule_catalog_unavailability_is_an_explicit_limitation():
    class Offline(AnalyticProjection):
        def rules(self):
            raise CalcEngineError("offline")

    result = run(log(), teamsheet=sheet(), port=Offline())
    assert not result.checkpoints and "unavailable" in result.limitations[0]


def test_legal_candidate_families_preserve_unmeasured_dimensions_and_total_budget():
    rules = AnalyticProjection().rules()
    declaration = DeclaredSet(participant="Alice", slot=1, species="Garchomp", stat_points={"atk": 32, "spe": 32})
    candidates = list(profiles("a:1", declaration, {"hp"}, rules))
    assert {entry.points["hp"] for entry in candidates} == {0, 1, 2}
    assert all("def" not in entry.points for entry in candidates)
    assert all(sum(entry.points.values()) <= 66 for entry in candidates)


def test_hp_precision_is_preserved_from_log_before_and_after_each_direct_hit():
    state = ShowdownReplayParser().parse(log())
    hit = next(event for event in state.outcome.events if event.kind == "move").hits[0]
    assert hit.hp_before_raw == "110/110" and hit.hp_after_raw == "48/110"


def test_consumed_item_does_not_carry_as_runtime_state_between_games():
    port = AnalyticProjection()
    first = log().replace("|turn|1", "|-enditem|p1a: Garchomp|Life Orb\n|turn|1")
    source = sheet()
    source["pokemon"][0]["item"] = "Life Orb"
    result = run(first, log(turn=2), teamsheet=source, port=port)
    assert result.checkpoints[-1].observation_ids
    items = {request.calc.attacker.item for request in port.requests}
    assert "" in items and "Life Orb" in items


def test_budget_exhaustion_in_a_later_game_does_not_erase_known_bounds():
    second = log(turn=2).replace("|turn|2", "|-weather|RainDance\n|turn|2")
    result = run(log(), second, teamsheet=sheet(), max_projections=33)
    assert result.checkpoints[1].coverage == "exhaustive"
    assert result.checkpoints[-1].coverage == "bounded"
    assert summary(result.checkpoints[-1], "Bob:slot1").stat_points["def"].minimum == 20
    assert summary(result.checkpoints[-1], "Bob:slot1").stat_points["def"].maximum == 20


def test_inference_never_uses_a_later_ability_reveal_for_an_earlier_hit():
    source = sheet()
    source["pokemon"][1].pop("ability")
    first = log().replace("|win|Alice", "|-ability|p2a: Incineroar|Intimidate\n|win|Alice")
    result = run(first, log(turn=2), teamsheet=source)
    assert not result.checkpoints[1].observation_ids
    assert result.checkpoints[-1].observation_ids
    assert all(observation.startswith("g2:") for observation in result.checkpoints[-1].observation_ids)


def test_a_mega_ability_is_not_remembered_as_the_base_ability_next_game():
    source = sheet()
    source["pokemon"][0].pop("ability")
    first = log().replace("|win|Alice", "|detailschange|p1a: Garchomp|Garchomp-Mega-Z, L50\n"
                                      "|-ability|p1a: Garchomp|Dragonize\n|win|Alice")
    result = run(first, log(turn=2), teamsheet=source)
    assert not result.checkpoints[-1].observation_ids


def test_conflicting_public_and_private_declarations_are_not_overwritten():
    source = sheet()
    source["pokemon"].append({"participant": "Alice", "slot": 1, "species": "Garchomp",
                              "stat_points": {"atk": 1}, "visibility": "owner_private"})
    with pytest.raises(SeriesValidationError, match="Conflicting"):
        run(log(), teamsheet=source, perspective="Alice")


def test_a_volatile_affecting_damage_is_excluded_instead_of_becoming_a_bad_spread():
    first = log().replace("|turn|1", "|-start|p2a: Incineroar|Substitute\n|turn|1")
    checkpoint = run(first, teamsheet=sheet()).checkpoints[-1]
    assert not checkpoint.observation_ids
    assert any("volatile" in reason for reason in checkpoint.unsupported)


def test_future_roster_identity_is_not_disclosed_before_its_first_game():
    first = log().replace("|poke|p1|Garchomp, L50|\n", "").replace("|poke|p2|Incineroar, L50|\n", "")
    second = log(winner="Bob", turn=2).replace("|poke|p1|Garchomp, L50|",
        "|poke|p1|Garchomp, L50|\n|poke|p1|Dragonite, L50|")
    result = run(first, second, teamsheet=sheet())
    assert all(entry.species != "Dragonite" for checkpoint in result.checkpoints[:2]
               for entry in checkpoint.summaries)
    assert any(entry.species == "Dragonite" for entry in result.checkpoints[2].summaries)


def test_bo3_service_passes_only_current_game_checkpoints_and_typed_final_evidence():
    from src.domain.models import AnalysisResult, MetaContext, SelectionPlan
    from src.services.series_analysis import Bo3AnalysisService
    from tests.conftest import PROMPTS, FakeLLM

    seen = []

    class Pipeline:
        def analyze(self, request):
            seen.append(request)
            return AnalysisResult(session_id=request.session_id, question=request.question,
                                  answer="review", selection=SelectionPlan(), meta_context=MetaContext())

    llm = FakeLLM("{}", "Alice and Bob each won one game.")
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection())
    service = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
                                llm=llm, prompts=PROMPTS, inference=coordinator)
    result = service.analyze(Bo3AnalysisRequest(session_id="integrated",
        games=[log(), log(winner="Bob", turn=2)], teamsheet=sheet(),
        inference_options=InferenceOptions(hp_mode="exact")))
    assert result.inference is not None
    assert len(result.inference.checkpoints) == 4
    for number, request in enumerate(seen, 1):
        checkpoints = request.series_context["set_inference"]["checkpoints"]
        assert len(checkpoints) == 2
        assert {checkpoint["game"] for checkpoint in checkpoints} == {number}
    content = llm.calls[-1]["messages"][-1].content
    payload = json.loads(content[:content.rfind("}") + 1])
    assert len(payload["set_inference"]["checkpoints"]) == 4
    assert payload["set_inference"]["units"] == "champions_stat_points"


def test_registered_item_conflict_is_rejected_before_pipeline_or_model_calls():
    from src.services.series_analysis import Bo3AnalysisService
    from tests.conftest import PROMPTS, FakeLLM

    def factory():
        pytest.fail("Invalid registrations must not invoke the paid pipeline")

    llm = FakeLLM("{}", "unused")
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection())
    service = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=factory,
                                llm=llm, prompts=PROMPTS, inference=coordinator)
    source = log().replace("|turn|1", "|-item|p1a: Garchomp|Life Orb|\n|turn|1")
    with pytest.raises(SeriesValidationError, match="contradicts"):
        service.analyze(Bo3AnalysisRequest(session_id="invalid", games=[source], teamsheet=sheet()))
    assert not llm.calls


def test_lru_eviction_does_not_discard_results_needed_by_the_current_batch():
    from src.domain.bo3_inference.models import HPReading, ProjectionRequest
    from src.domain.models import CalcRequest, PokemonSet
    from src.services.bo3_inference.solver import ProjectionBudget

    budget = ProjectionBudget(AnalyticProjection(), InferenceOptions(max_cache_entries=64))
    calc = CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp"),
                       defender=PokemonSet(species="Incineroar"), move="Dragon Claw")
    hp = HPReading(numerator=100, denominator=100, mode="exact")
    requests = [ProjectionRequest(calc=calc, attacker_points={"atk": index % 33},
                    defender_points={"def": index // 33}, before=hp, attacker_before=hp)
                for index in range(96)]
    budget.project(requests[:64])
    # Recently touched cached values can be evicted by the new half of this batch.
    result = budget.project(requests[:32] + requests[64:])
    assert len(result) == 64 and all(entry.supported for entry in result)
    assert len(budget.cache) == 64
    assert budget.projections == 96
