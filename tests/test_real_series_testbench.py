"""Recorded BO3 regression matrix, with known spreads held out from inference."""

import random
import re

import pytest

import scripts.bo3_real_series_benchmark as benchmark
from scripts.bo3_real_series_benchmark import (
    FLOETTE,
    collect,
    conditional_spa_sweep,
    held_out_profiles,
    matching_witnesses,
    recorded_games,
    report,
)
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.constraints import agrees
from src.domain.bo3_inference.citations import cite_observation, describe_hit
from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.models import (
    DamageSupport,
    DamageWitness,
    HPReading,
    InferenceOptions,
)
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import visible_declarations
from src.services.bo3_inference.propagation import table_request
from src.services.bo3_inference.solver import projection_request
from tests.test_bo3_inference_engine import engine  # noqa: F401


@pytest.fixture(scope="module")
def real_case(engine):  # noqa: F811
    data = collect(engine)
    return data, held_out_profiles(data[4])


# IDs are frozen against the recorded logs, so a lost eligible hit fails visibly.
HIT_IDS = [
    "g1:e12:p2:Whimsicott", "g1:e18:p1:Rillaboom", "g1:e19:p1:Rillaboom", "g1:e19:p1:Incineroar",
    "g1:e28:p1:Raichu", "g1:e29:p1:Raichu", "g1:e30:p2:Whimsicott", "g1:e36:p1:Incineroar",
    "g1:e40:p1:Gholdengo", "g1:e41:p1:Gholdengo",
    "g2:e12:p2:Floette-Eternal", "g2:e13:p1:Garchomp", "g2:e14:p2:Garchomp",
    "g2:e17:p1:Gholdengo", "g2:e17:p1:Incineroar", "g2:e23:p1:Rillaboom", "g2:e30:p2:Kingambit",
    "g2:e31:p1:Gholdengo", "g2:e32:p2:Basculegion", "g2:e32:p2:Kingambit",
    "g2:e43:p2:Floette-Eternal", "g2:e44:p2:Floette-Eternal", "g2:e45:p1:Rillaboom",
    "g2:e51:p1:Rillaboom", "g2:e58:p2:Kingambit", "g2:e59:p2:Basculegion",
    "g2:e70:p1:Garchomp", "g2:e76:p2:Kingambit",
    "g3:e9:p2:Floette-Eternal", "g3:e16:p1:Volcarona", "g3:e25:p2:Basculegion",
    "g3:e25:p2:Kingambit", "g3:e28:p1:Volcarona", "g3:e31:p2:Kingambit",
    "g3:e42:p1:Gholdengo", "g3:e43:p2:Whimsicott", "g3:e45:p2:Basculegion",
    "g3:e45:p2:Whimsicott", "g3:e49:p1:Gholdengo", "g3:e50:p1:Volcarona",
    "g3:e58:p1:Garchomp", "g3:e59:p1:Incineroar", "g3:e63:p1:Incineroar", "g3:e66:p1:Incineroar",
]


def find(real_case, identifier):
    return next(hit for hit in real_case[0][5] if hit.id == identifier)


def test_real_hit_citation_preserves_move_owner_target_and_displayed_hp(real_case):
    hit = find(real_case, "g2:e43:p2:Floette-Eternal")
    citation = cite_observation(hit, relevant_axes([hit]))
    assert citation.attacker_id == "testProfessor:slot5"
    assert citation.defender_id == "Huarada:slot3"
    assert describe_hit(citation) == (
        "game 2 event 43: Grassy Glide by Rillaboom into Floette-Eternal, 97% -> 32%"
    )
    assert "hp" in citation.dependent_axes["Huarada:slot3"]


@pytest.mark.parametrize("identifier", HIT_IDS)
def test_each_recorded_damage_retains_a_known_spread_witness(real_case, engine, identifier):  # noqa: F811
    hit = find(real_case, identifier)
    support = engine.project_batch([projection_request(hit, real_case[1])])[0]
    assert support.supported, support.reason
    assert matching_witnesses(hit, support), identifier


def swap_sides(log):
    return re.sub(r"\bp([12])(?=[ab]?(?::|\|))", lambda match: "p" + str(3 - int(match[1])), log)


def rename_actors(log):
    names = {}
    def replace(match):
        actor = match[0]
        # A Pokemon can switch between active slots; its nickname must persist.
        key = (actor[:2], actor.split(": ", 1)[1])
        names.setdefault(key, "nickname" + str(len(names)))
        return actor.split(":")[0] + ": " + names[key]
    return re.sub(r"p[12][ab]: [^|\n]+", replace, log)


@pytest.mark.parametrize("variant", ["swap_all", "swap_middle", "nicknames", "both"])
def test_real_damage_requests_are_invariant_to_sides_and_nicknames(real_case, engine, variant):  # noqa: F811
    games = [game["log"] for game in recorded_games()]
    transformed = []
    for game, log in enumerate(games, 1):
        if variant in {"swap_all", "both"} or variant == "swap_middle" and game == 2:
            log = swap_sides(log)
        if variant in {"nicknames", "both"}:
            log = rename_actors(log)
        transformed.append(log)
    assert transformed != games
    alternate = collect(engine, transformed)
    assert not alternate[-1]
    assert alternate[1].score == real_case[0][1].score
    def fingerprint(hits):
        return {(hit.game, hit.event_index, hit.attacker_id, hit.defender_id):
                hit.model_dump(exclude={"id"}) for hit in hits}
    assert fingerprint(alternate[5]) == fingerprint(real_case[0][5])


@pytest.mark.parametrize("effect,identifier,direction", [
    ("aura", "g1:e18:p1:Rillaboom", -1),
    ("burn", "g3:e42:p1:Gholdengo", 1),
    ("faint_counter", "g3:e42:p1:Gholdengo", -1),
    ("spread", "g3:e25:p2:Kingambit", 1),
    ("boost", "g2:e32:p2:Kingambit", -1),
    ("life_orb", "g3:e25:p2:Kingambit", -1),
    ("terrain", "g2:e30:p2:Kingambit", -1),
])
def test_removing_a_real_causal_effect_changes_native_damage(real_case, engine, effect, identifier, direction):  # noqa: F811
    hit = find(real_case, identifier)
    original = projection_request(hit, real_case[1])
    calc = original.calc
    if effect == "aura":
        assert calc.field.fairy_aura and calc.attacker.species == "Whimsicott"
        calc = calc.model_copy(update={"field": calc.field.model_copy(update={"fairy_aura": False})})
    elif effect == "burn":
        assert calc.attacker.status == "brn"
        calc = calc.model_copy(update={"attacker": calc.attacker.model_copy(update={"status": ""})})
    elif effect == "faint_counter":
        assert calc.attacker_allies_fainted == 1
        calc = calc.model_copy(update={"attacker_allies_fainted": 0})
    elif effect == "spread":
        assert calc.spread_hit
        calc = calc.model_copy(update={"spread_hit": False})
    elif effect == "boost":
        assert calc.attacker.boosts["spa"] == 2
        calc = calc.model_copy(update={"attacker": calc.attacker.model_copy(update={"boosts": {}})})
    elif effect == "life_orb":
        assert calc.attacker.item == "LifeOrb"
        calc = calc.model_copy(update={"attacker": calc.attacker.model_copy(update={"item": ""})})
    else:
        assert calc.field.terrain == "Grassy"
        calc = calc.model_copy(update={"field": calc.field.model_copy(update={"terrain": None})})
    baseline, changed = engine.project_batch([original, original.model_copy(update={"calc": calc})])
    assert baseline.supported and changed.supported, changed.reason
    assert matching_witnesses(hit, baseline)
    for endpoint in (min, max):
        before = endpoint(w.damage for w in baseline.witnesses)
        after = endpoint(w.damage for w in changed.witnesses)
        assert (after - before) * direction > 0


def test_real_healing_residual_and_recoil_are_not_counted_as_direct_attack_damage(real_case):
    # Sitrus heals Incineroar from 12 to 37 before the final Wave Crash.
    moonblast = find(real_case, "g3:e63:p1:Incineroar")
    final_wave = find(real_case, "g3:e66:p1:Incineroar")
    assert (moonblast.before.numerator, moonblast.after.numerator) == (59, 12)
    assert final_wave.before.numerator == 37 and final_wave.after.numerator == 0
    # Basculegion's recoil/burn changes its own HP, not its target's damage.
    first_wave = find(real_case, "g3:e59:p1:Incineroar")
    assert first_wave.attacker_before.numerator == 30
    assert final_wave.attacker_before.numerator == 12
    assert (first_wave.before.numerator, first_wave.after.numerator) == (100, 59)


def test_mega_abilities_and_spa_drops_follow_the_recorded_event_not_the_sheet(real_case):
    early = find(real_case, "g1:e19:p1:Incineroar")
    late = find(real_case, "g1:e36:p1:Incineroar")
    next_game = find(real_case, "g2:e17:p1:Incineroar")
    assert early.request.attacker.ability == late.request.attacker.ability == "Fairy Aura"
    assert early.request.attacker.battle_formes == ["Floette-Mega"]
    assert early.request.attacker.boosts.get("spa", 0) == 0
    assert late.request.attacker.boosts["spa"] == -1
    assert next_game.request.attacker.boosts.get("spa", 0) == 0
    raichu = find(real_case, "g1:e12:p2:Whimsicott")
    assert raichu.request.attacker.ability == "No Guard"
    assert raichu.request.attacker.battle_formes == ["Raichu-Mega-Y"]


def test_conditional_floette_candidates_only_shrink_and_name_the_excluding_hits(real_case, engine):  # noqa: F811
    sweep = conditional_spa_sweep(engine, real_case[0][5], real_case[1])
    assert [checkpoint["compatible_spa"] for checkpoint in sweep] == [
        list(range(17, 33)), list(range(27, 33)), list(range(27, 33))]
    previous = set(range(33))
    for checkpoint in sweep:
        remaining = set(checkpoint["compatible_spa"])
        assert 32 in remaining and remaining <= previous
        assert previous - remaining == {int(value) for value in checkpoint["rejected_by"]}
        assert all(identifier.startswith(f"g{checkpoint['game']}:")
                   for reasons in checkpoint["rejected_by"].values() for identifier in reasons)
        previous = remaining
    assert previous == set(range(27, 33))


@pytest.mark.parametrize("first,second", [
    ("g1:e18:p1:Rillaboom", "g1:e19:p1:Rillaboom"),
    ("g1:e28:p1:Raichu", "g1:e29:p1:Raichu"),
])
def test_consecutive_attacks_share_a_real_integer_hp_witness(real_case, engine, first, second):  # noqa: F811
    # These recorded pairs have no intervening heal/residual HP change. Join
    # integer witnesses, rather than independently matching two rounded readings.
    left, right = find(real_case, first), find(real_case, second)
    assert left.defender_id == right.defender_id and left.after == right.before
    supports = engine.project_batch([projection_request(hit, real_case[1]) for hit in (left, right)])
    witnesses = [matching_witnesses(hit, support) for hit, support in zip((left, right), supports)]
    assert {row["hp_after"] for row in witnesses[0]} & {row["hp_before"] for row in witnesses[1]}


@pytest.mark.parametrize("failure", ["wrong_known_spread", "unsupported_projection"])
def test_real_report_fails_visibly_instead_of_dropping_bad_damage(real_case, engine, monkeypatch, failure):  # noqa: F811
    if failure == "wrong_known_spread":
        wrong = dict(real_case[1])
        floette = wrong[FLOETTE]
        wrong[FLOETTE] = floette.model_copy(update={"points": {**floette.points, "spa": 0, "atk": 32}})
        monkeypatch.setattr(benchmark, "held_out_profiles", lambda initial: wrong)
    else:
        original = engine.project_batch
        def unavailable(requests):
            values = original(requests)
            if values:
                values[0] = values[0].model_copy(update={"supported": False, "reason": "testbench unavailable"})
            return values
        monkeypatch.setattr(engine, "project_batch", unavailable)
    result = report(engine)
    assert not result["passed"]
    assert len(result["damage"]) == 44
    assert result["summary"]["passed_observations"] < 44
    assert any(not row["passed"] and row["id"] for row in result["damage"])
    if failure == "unsupported_projection":
        assert result["damage"][0]["reason"] == "testbench unavailable"
        # A missing model is uncertainty, never evidence excluding a spread.
        first = result["conditional_floette_spa"][0]
        assert first["unresolved_by"] and first["unresolved_spa"]
        assert set(first["unresolved_spa"]) <= set(first["not_ruled_out_spa"])
        assert not set(first["unresolved_by"]) & set(first["rejected_by"])


def test_real_public_checkpoints_preserve_truth_and_never_consult_future_games(real_case, engine):  # noqa: F811
    request, evidence, states, sheet, initial, hits, _ = real_case[0]
    assert request.teamsheet is None and all(not entry.stat_points for entry in sheet.pokemon)
    # The longest allowed deadline keeps table coverage independent of machine load.
    options = InferenceOptions(max_projections=12000, max_assignments=12000, timeout_seconds=120)
    request = request.model_copy(update={"inference_options": options})
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), public_sheets=ShowdownTeamSheetParser(),
        projection=engine).analyze(request, evidence, states)
    previous = None
    for checkpoint in result.checkpoints:
        cutoff = checkpoint.game - (checkpoint.phase == "before_game")
        assert set(checkpoint.observation_ids) == {hit.id for hit in hits if hit.game <= cutoff}
        assert checkpoint.outcome != "conflict"
        for summary in checkpoint.summaries:
            for stat, bound in summary.stat_points.items():
                assert bound.minimum <= real_case[1][summary.pokemon_id].points[stat] <= bound.maximum
                assert real_case[1][summary.pokemon_id].points[stat] not in summary.excluded_points.get(stat, ())
                if previous:
                    old = previous[summary.pokemon_id].stat_points[stat]
                    assert old.minimum <= bound.minimum <= bound.maximum <= old.maximum
        previous = {summary.pokemon_id: summary for summary in checkpoint.summaries}
    assert result.projections <= options.max_projections
    gholdengo = previous["testProfessor:slot1"].stat_points["hp"]
    assert 0 < gholdengo.minimum <= 27 <= gholdengo.maximum < 32
    assert result.checkpoints[-1].local_constraints
    # Both players have Garchomp: stable owner/slot identity must separate them.
    assert initial["Huarada:slot6"].species == initial["testProfessor:slot3"].species == "Garchomp"
    assert real_case[1]["Huarada:slot6"].natures == ("Jolly",)
    assert real_case[1]["testProfessor:slot3"].natures == ("Modest",)
    # Charizard is registered but never brought in these three games. Knowing
    # its reference spread must not make its public bounds falsely precise.
    charizard = "Huarada:slot5"
    assert not any(charizard in (hit.attacker_id, hit.defender_id) for hit in hits)
    unobserved = previous[charizard]
    assert unobserved.basis == "declarations"
    assert all((bound.minimum, bound.maximum) == (0, 32) for bound in unobserved.stat_points.values())
    # ADR-068 per-hit tables: 19 narrowed stats on this series (legacy at this cap: 1).
    narrowed = [(key, stat) for key, summary in previous.items() for stat, bound in summary.stat_points.items()
                if (bound.minimum, bound.maximum) != (0, 32)]
    assert len(narrowed) >= 19
    whimsicott = previous["Huarada:slot1"]
    assert (whimsicott.stat_points["hp"].minimum, whimsicott.stat_points["hp"].maximum) == (2, 29)
    assert whimsicott.single_observation_bounds["hp"] == ("g3:e43:p2:Whimsicott",)


def test_real_hit_tables_match_the_batch_path_on_sampled_cells(real_case, engine):  # noqa: F811
    _, _, _, sheet, initial, _, _ = real_case[0]
    declarations = visible_declarations(initial, sheet, 3, "public")
    hit = find(real_case, "g3:e43:p2:Whimsicott")
    built = table_request(hit, declarations)
    assert built is not None
    variables, request = built
    cells = engine.project_table(request).cells
    assert len(cells) == 33 ** len(variables) and set(cells) <= {"0", "1"}
    sample = random.Random(68).sample(range(len(cells)), 256)
    requests = []
    for index in sample:
        points = {"attacker": dict(request.base.attacker_points), "defender": dict(request.base.defender_points)}
        for position, axis in enumerate(request.axes):
            points[axis.side][axis.stat] = index // 33 ** (len(request.axes) - 1 - position) % 33
        requests.append(request.base.model_copy(update={"attacker_points": points["attacker"],
                                                        "defender_points": points["defender"]}))
    supports = [support for offset in range(0, len(requests), 64)
                for support in engine.project_batch(requests[offset:offset + 64])]
    assert [cells[index] for index in sample] == ["1" if agrees(hit, support) else "0" for support in supports]


def test_real_benchmark_report_exposes_provenance_ambiguity_and_all_failed_hits(engine):  # noqa: F811
    result = report(engine)
    assert result["passed"] and result["summary"]["observations"] == 44
    assert result["summary"]["floette_observations"] == 15
    assert len(result["provenance"]["public_sha256"]) == len(result["provenance"]["truth_sha256"]) == 64
    assert {row["id"] for row in result["damage"]} == set(HIT_IDS)
    assert result["limitations"] and len(result["conditional_floette_spa"][-1]["compatible_spa"]) > 1
    public = result["blind_public_search"]
    assert public["projections"] <= public["options"]["max_projections"] == 12000
    assert [checkpoint["observations"] for checkpoint in public["checkpoints"]] == [10, 28, 44]
    assert all(checkpoint["truth_retained"] for checkpoint in public["checkpoints"])
    assert all(checkpoint["certified_constraints"] for checkpoint in public["checkpoints"])
    final_hp = public["checkpoints"][-1]["narrowed"]["testProfessor:slot1"]["hp"]
    assert 0 < final_hp["minimum"] <= 27 <= final_hp["maximum"] < 32
    assert any(row["censored_ko"] for row in result["damage"])
    assert any(row["after"]["color"] == "y" for row in result["damage"])
    assert FLOETTE in {row["attacker"] for row in result["damage"]}
    assert result["checks"] and all(result["checks"].values())


@pytest.mark.parametrize("hp,raw,expected", [
    (170, "100/100", True), (169, "100/100", False),
    (1, "1/100", True), (3, "1/100", True), (4, "1/100", False),
    (35, "20/100y", True), (34, "20/100y", False), (34, "20/100r", True),
    (86, "50/100g", True), (85, "50/100g", False), (85, "50/100y", True),
    (0, "0/100", True), (1, "0/100", False), (171, "100/100", False),
])
def test_independent_hp_oracle_against_fixed_integer_examples(hp, raw, expected):
    match = re.fullmatch(r"(\d+)/(\d+)([gyr])?", raw)
    reading = HPReading.model_validate({"numerator": int(match[1]), "denominator": int(match[2]),
        "color": match[3], "mode": "percent"})
    assert benchmark.matches_hp(hp, 170, reading) is expected


@pytest.mark.parametrize("before,damage,valid", [(99, 48, False), (101, 50, False), (100, 49, True)])
def test_after_hp_agreement_cannot_hide_an_invalid_before_hp(real_case, before, damage, valid):
    hit = find(real_case, "g1:e12:p2:Whimsicott")  # Public HP 100% -> 51%.
    support = DamageSupport(supported=True, maximum_hp=100,
        witnesses=(DamageWitness(hp_before=before, damage=damage),))
    assert bool(matching_witnesses(hit, support)) is valid


@pytest.mark.parametrize("mutation", ["drop_one", "duplicate_one", "empty"])
def test_report_rejects_incomplete_or_duplicate_coverage_even_if_remaining_hits_match(engine, monkeypatch, mutation):  # noqa: F811
    original = benchmark.collect
    def changed(adapter):
        data = list(original(adapter))
        hits = data[5]
        data[5] = hits[1:] if mutation == "drop_one" else hits + hits[:1] if mutation == "duplicate_one" else []
        return tuple(data)
    monkeypatch.setattr(benchmark, "collect", changed)
    result = report(engine)
    assert not result["passed"] and not result["checks"]["recorded_coverage"]
    assert all(row["passed"] for row in result["damage"])


def test_public_narrowing_without_a_certificate_does_not_pass_the_report(engine, monkeypatch):  # noqa: F811
    original = Bo3InferenceCoordinator.analyze
    def uncertified(self, request, evidence, states):
        result = original(self, request, evidence, states)
        return result.model_copy(update={"checkpoints": tuple(
            checkpoint.model_copy(update={"local_constraints": ()}) for checkpoint in result.checkpoints)})
    monkeypatch.setattr(Bo3InferenceCoordinator, "analyze", uncertified)
    result = report(engine)
    assert not result["passed"] and not result["checks"]["certified_narrowing"]
    assert result["checks"]["all_damage_matches"] and result["checks"]["truth_retained"]


def test_real_native_damage_is_repeatable_for_identical_causal_inputs(real_case, engine):  # noqa: F811
    requests = [projection_request(hit, real_case[1]) for hit in real_case[0][5]]
    assert len(requests) == 44
    assert engine.project_batch(requests) == engine.project_batch(requests)


def test_reference_spreads_are_loaded_only_after_the_public_search_finishes(engine, monkeypatch):  # noqa: F811
    search_finished = []
    original_analyze = Bo3InferenceCoordinator.analyze
    original_profiles = benchmark.held_out_profiles
    def analyze(self, request, evidence, states):
        assert request.teamsheet is None
        result = original_analyze(self, request, evidence, states)
        search_finished.append(True)
        return result
    def reference(initial):
        assert search_finished == [True]
        return original_profiles(initial)
    monkeypatch.setattr(Bo3InferenceCoordinator, "analyze", analyze)
    monkeypatch.setattr(benchmark, "held_out_profiles", reference)
    assert report(engine)["passed"]
