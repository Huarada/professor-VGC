"""Public export retrieval, causal visibility and useful bounded inference."""

import json
from pathlib import Path

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser, parse_replay_for_viewer
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.models import DamageWitness, InferenceOptions
from src.domain.exceptions import SeriesValidationError
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, merge_sheets, visible_declarations
from src.services.series_analysis import prepare_series
from tests.test_bo3_inference import AnalyticProjection, log, run, sheet, summary

PUBLIC_FIXTURE = Path(__file__).parent / "fixtures/series/bo3-public-showteam-2702587956.json"
SAURON_FIXTURE = Path(__file__).parent / "fixtures/series/bo3-public-showteam-2702605271.json"


def public_games():
    return json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))["games"]


def sauron_games():
    return json.loads(SAURON_FIXTURE.read_text(encoding="utf-8"))["games"]


def test_sauron_series_recovers_twelve_natures_and_preserves_real_redirection_protocol():
    games = sauron_games()
    sheet = ShowdownTeamSheetParser().parse(games)
    assert sheet.registered_team and len(sheet.pokemon) == 36
    for number in range(1, 4):
        entries = [entry for entry in sheet.pokemon if entry.only_game == number]
        assert len(entries) == 12 and all(entry.nature for entry in entries)
        assert all(not entry.stat_points and not entry.ivs for entry in entries)
    request = Bo3AnalysisRequest(session_id="sauron", games=games)
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    assert evidence.complete and evidence.score == {"sauronMobile": 1, "miro kart ds": 2}
    for source, state in zip(games, states):
        assert "|move: Rage Powder" in source["log"]
        assert not state.inference_exclusions
        assert parse_replay_for_viewer(source).snapshots


def packed_log(*, nature="Hardy", late=False, root=True):
    lines = log().splitlines()
    source = "\n".join([lines[0]] + [line for line in lines if line.startswith("|player|")]
                       + [line for line in lines[1:] if not line.startswith("|player|")])
    sheets = (f"|showteam|p1|Garchomp|||Rough Skin|DragonClaw|{nature}||M|||50|\n"
              "|showteam|p2|Incineroar|||Intimidate|Protect|Hardy||M|||50|\n")
    marker = "|win|Alice" if late else "|switch|p1a: Garchomp"
    source = source.replace(marker, sheets + marker)
    if root:
        source += '\n|uhtml|bestof|<h2>Game 1 of <a href="/game-bestof3-gen9championsvgc2026regmbbo3-123">a best-of-3</a></h2>'
    return source


def test_reported_series_retrieves_all_twelve_public_natures_in_every_game():
    result = ShowdownTeamSheetParser().parse(public_games())
    assert result.registered_team and len(result.pokemon) == 36
    expected = {"Delphox": "Timid", "Annihilape": "Adamant", "Sylveon": "Modest", "Incineroar": "Careful",
                "Rillaboom": "Adamant", "Metagross": "Adamant", "Indeedee": "Modest", "Blaziken": "Modest",
                "Dragapult": "Timid", "Excadrill": "Adamant", "Tyranitar": "Adamant", "Corviknight": "Adamant"}
    for number in range(1, 4):
        entries = [entry for entry in result.pokemon if entry.only_game == number]
        assert {entry.species: entry.nature for entry in entries} == expected
        assert all(not entry.stat_points and not entry.ivs for entry in entries)
        assert all(entry.field_sources["nature"].visibility == "public" for entry in entries)


def test_public_sheet_is_retrieved_even_when_the_request_has_no_manual_sheet():
    request = Bo3AnalysisRequest(session_id="auto", games=[packed_log()])
    evidence, games = prepare_series(ShowdownReplayParser(), request)
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection(),
                                         public_sheets=ShowdownTeamSheetParser())
    result = coordinator.analyze(request, evidence, games)
    assert result.continuity == "registered_team"
    checkpoint = result.checkpoints[-1]
    assert checkpoint.observation_ids  # Nature/investment unknown does not skip the hit.
    assert all(entry.natures == ("Hardy",) for entry in checkpoint.summaries)


def test_manual_private_investment_merges_with_public_facts_and_slot_identity():
    public = ShowdownTeamSheetParser().parse([packed_log()])
    manual = JsonTeamSheetParser().parse({"pokemon": [
        {"participant": "Alice", "slot": 4, "species": "Garchomp", "visibility": "owner_private",
         "stat_points": {"atk": 32}},
    ]})
    combined = merge_sheets(manual, public)
    assert all(entry.slot == 4 for entry in combined.pokemon if entry.participant == "Alice")
    request = Bo3AnalysisRequest(session_id="private", games=[packed_log()])
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    initial, _ = identities(evidence, states, combined)
    public_view = visible_declarations(initial, combined, 1, "public")
    owner_view = visible_declarations(initial, combined, 1, "Alice")
    assert public_view["Alice:slot4"].nature == "Hardy"
    assert public_view["Alice:slot4"].stat_points == {}
    assert owner_view["Alice:slot4"].stat_points == {"atk": 32}


def test_user_sheet_cannot_silently_override_a_public_nature():
    request = Bo3AnalysisRequest(session_id="conflict", games=[packed_log()], teamsheet={"pokemon": [
        {"participant": "Alice", "slot": 1, "species": "Garchomp", "nature": "Adamant"},
    ]})
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection(),
                                         public_sheets=ShowdownTeamSheetParser())
    with pytest.raises(SeriesValidationError, match="Conflicting declared nature"):
        coordinator.validate(request, evidence, states)


def test_late_or_chat_teamsheets_do_not_leak_into_earlier_moves():
    late = packed_log(late=True)
    assert not ShowdownTeamSheetParser().parse([late]).pokemon
    chat = log() + '\n|c|Guest||showteam|p1|Garchomp|||Rough Skin|DragonClaw|Adamant||M|||50|'
    assert not ShowdownTeamSheetParser().parse([chat]).pokemon


def test_missing_nature_stays_unknown_but_damage_is_still_evaluated():
    request = Bo3AnalysisRequest(session_id="unknown-nature", games=[packed_log(nature="")],
                                 inference_options=InferenceOptions(max_assignments=3000))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    coordinator = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection(),
                                         public_sheets=ShowdownTeamSheetParser())
    result = coordinator.analyze(request, evidence, states)
    attacker = summary(result.checkpoints[0], "Alice:slot1")
    assert not attacker.nature_declared and len(attacker.natures) == 3
    assert result.checkpoints[-1].observation_ids and result.projections > 0


def test_changed_registered_public_nature_is_rejected_but_unregistered_can_vary():
    with pytest.raises(SeriesValidationError, match="changed between"):
        ShowdownTeamSheetParser().parse([packed_log(), packed_log(nature="Adamant")])
    result = ShowdownTeamSheetParser().parse([packed_log(root=False), packed_log(nature="Adamant", root=False)])
    assert not result.registered_team
    assert result.pokemon[0].only_game == 1 and result.pokemon[2].only_game == 2


def test_reimported_public_json_does_not_duplicate_sources():
    public = ShowdownTeamSheetParser().parse(public_games())
    manual = JsonTeamSheetParser().parse(public.model_dump_json())
    combined = merge_sheets(manual, public)
    assert len(combined.pokemon) == 36


def test_completed_local_constraint_narrows_ranges_without_claiming_global_uniqueness():
    exhaustive = run(log(damage=60), teamsheet=sheet(known_attack=False))
    bounded = run(log(damage=60), teamsheet=sheet(known_attack=False), max_assignments=500)
    final = bounded.checkpoints[-1]
    assert final.coverage == "bounded" and final.outcome == "ambiguous"
    assert final.local_constraints and final.local_evaluated_assignments == 1089
    for key, axis in (("Alice:slot1", "atk"), ("Bob:slot1", "def")):
        actual = summary(final, key)
        assert actual.stat_points[axis] == summary(exhaustive.checkpoints[-1], key).stat_points[axis]
        assert actual.basis == "necessary_local_bounds"
    assert final.compatible_families is None


def test_protect_and_grassy_terrain_do_not_disable_unrelated_later_damage():
    source = log().replace("|turn|1", "\n".join([
        "|-singleturn|p2a: Incineroar|Protect",
        "|-fieldstart|move: Grassy Terrain|[from] ability: Grassy Surge|[of] p2a: Incineroar", "|turn|1",
    ]))
    declared = sheet()
    declared["pokemon"][1]["ability"] = "Grassy Surge"
    result = run(source, teamsheet=declared)
    final = result.checkpoints[-1]
    assert final.observation_ids and final.outcome == "identified_within_model"
    assert not any("Protect" in reason or "Grassy Surge" in reason for reason in final.unsupported)


def test_unknown_volatile_remains_excluded_instead_of_becoming_a_false_constraint():
    source = log().replace("|turn|1", "|-start|p2a: Incineroar|Substitute\n|turn|1")
    result = run(source, teamsheet=sheet())
    assert not result.checkpoints[-1].observation_ids
    assert any("Substitute" in reason for reason in result.checkpoints[-1].unsupported)


@pytest.mark.parametrize("effect", [
    "|-start|p1a: Garchomp|move: Encore",
    "|-end|p1a: Garchomp|Encore",
    "|-start|p1a: Garchomp|perish3|[silent]",
    "|-start|p2a: Incineroar|perish0|[silent]",
    "|-singleturn|p1a: Garchomp|Rage Powder",
    "|-singleturn|p1a: Garchomp|Follow Me",
    "|-singleturn|p1a: Garchomp|move: Rage Powder",
    "|-singleturn|p1a: Garchomp|move: Follow Me",
    "|-singleturn|p1a: Garchomp|move: Protect",
    "|-start|p1a: Garchomp|Throat Chop",
    "|-end|p1a: Garchomp|Throat Chop",
    "|-start|p1a: Garchomp|Disable|Dragon Claw",
])
def test_selection_redirection_and_perish_counters_preserve_observed_direct_damage(effect):
    source = log().replace("|turn|1", effect + "\n|turn|1")
    result = run(source, teamsheet=sheet())
    checkpoint = result.checkpoints[-1]
    assert checkpoint.observation_ids
    assert checkpoint.outcome == "identified_within_model"
    assert summary(checkpoint, "Bob:slot1").stat_points["def"].minimum == 20


def test_local_factor_intersects_repeated_hits_instead_of_ignoring_the_second_hit():
    first = log(damage=60)
    second_hit = "\n".join([
        "|turn|2", "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        "|-damage|p2a: Incineroar|0 fnt", "|win|Alice",
    ])
    source = first.replace("|win|Alice", second_hit)
    result = run(source, teamsheet=sheet(known_attack=False), max_assignments=500)
    checkpoint = result.checkpoints[-1]
    assert len(checkpoint.observation_ids) == 2
    assert set(checkpoint.local_constraints) == set(checkpoint.observation_ids)
    assert checkpoint.coverage == "bounded" and checkpoint.outcome == "ambiguous"
    # The censored KO is compatible with the first observed 60 damage.
    assert summary(checkpoint, "Alice:slot1").stat_points["atk"].minimum == 10


def test_two_locally_possible_samples_can_conflict_when_jointly_constrained():
    second_hit = "\n".join([
        "|turn|2", "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        "|-damage|p2a: Incineroar|10/110", "|win|Alice",
    ])
    source = log(damage=60).replace("|win|Alice", second_hit)
    result = run(source, teamsheet=sheet(known_attack=False), max_assignments=500)
    checkpoint = result.checkpoints[-1]
    assert len(checkpoint.local_constraints) == 2
    assert checkpoint.outcome == "conflict"


def test_multiple_roll_samples_narrow_bounds_without_using_future_games():
    class RollProjection(AnalyticProjection):
        def project_batch(self, requests):
            return [support.model_copy(update={"witnesses": tuple(
                DamageWitness(hp_before=witness.hp_before, damage=witness.damage + roll)
                for witness in support.witnesses for roll in range(6)
            )}) for support in super().project_batch(requests)]

    result = run(log(damage=60), log(damage=65, winner="Bob"), teamsheet=sheet(known_attack=False),
                 port=RollProjection(), max_assignments=500)
    first, before_second, second = result.checkpoints[1:]
    assert summary(first, "Alice:slot1").stat_points["atk"].minimum == 5
    assert summary(before_second, "Alice:slot1").stat_points["atk"].minimum == 5
    assert summary(second, "Alice:slot1").stat_points["atk"].minimum == 10
    assert summary(second, "Bob:slot1").stat_points["def"].maximum == 22
    assert len(second.local_constraints) == 2
    assert second.coverage == "bounded"


def test_unregistered_teams_never_combine_damage_from_different_games():
    result = run(log(damage=60), log(damage=65, winner="Bob"),
                 teamsheet=sheet(registered=False, known_attack=False), max_assignments=500)
    last = result.checkpoints[-1]
    assert last.outcome == "ambiguous"
    assert len(last.local_constraints) == 1
    assert all(identifier.startswith("g2:") for identifier in last.local_constraints)


def test_unaffordable_full_group_uses_a_completed_two_sample_subset():
    extra = []
    for turn in (2, 3):
        extra.extend([
            "|-heal|p2a: Incineroar|110/110|[from] move: Recover",
            f"|turn|{turn}", "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
            "|-damage|p2a: Incineroar|50/110",
        ])
    source = log(damage=60).replace("|win|Alice", "\n".join(extra + ["|win|Alice"]))
    result = run(source, teamsheet=sheet(known_attack=False), max_assignments=500, max_projections=2600)
    checkpoint = result.checkpoints[-1]
    assert len(checkpoint.observation_ids) == 3
    assert len(checkpoint.local_constraints) == 2
    assert checkpoint.local_evaluated_assignments == 1089
    assert checkpoint.coverage == "bounded"
