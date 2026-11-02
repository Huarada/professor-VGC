"""Causal HP, series identity, runtime traits and conservative inference bounds."""

import copy
import json

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.bo3_inference.constraints import agrees, displays
from src.domain.bo3_inference.models import (
    DamageObservation,
    DamageSupport,
    DamageWitness,
    HPReading,
    PointRange,
)
from src.domain.bo3_inference.narration import coaching_context
from src.domain.bo3_inference.session import retain_known_bounds
from src.domain.exceptions import SeriesValidationError
from src.domain.models import CalcRequest, PokemonSet
from src.domain.series import Bo3AnalysisRequest
from src.services.series_analysis import prepare_series
from tests.test_attribute_ranges import checkpoint
from tests.test_bo3_analysis import game, prepare
from tests.test_bo3_inference import log, run, sheet


def test_duplicate_battle_cannot_hide_behind_different_json_ids_or_ratings():
    sources = [{"id": "one", "log": game(), "rating": 1500},
               {"id": "two", "log": game(), "rating": 1700}]
    with pytest.raises(SeriesValidationError, match="more than once"):
        prepare_series(ShowdownReplayParser(), Bo3AnalysisRequest(session_id="duplicate", games=sources))


def test_duplicate_raw_and_json_battle_are_rejected():
    sources = [game(), {"id": "one", "log": game()}]
    with pytest.raises(SeriesValidationError, match="more than once"):
        prepare_series(ShowdownReplayParser(), Bo3AnalysisRequest(session_id="duplicate", games=sources))


def test_same_replay_id_is_rejected_even_for_pasted_json_with_different_content():
    sources = [json.dumps({"id": "same", "log": game()}),
               json.dumps({"id": "same", "log": game("Bob", "Protect")})]
    with pytest.raises(SeriesValidationError, match="more than once"):
        prepare_series(ShowdownReplayParser(), Bo3AnalysisRequest(session_id="duplicate", games=sources))


def test_reversed_battle_timestamps_are_rejected_before_analysis():
    with pytest.raises(SeriesValidationError, match="chronological"):
        prepare("|t:|200\n" + game(), "|t:|100\n" + game("Bob", "Protect"))


def test_first_battle_clock_is_preserved_and_missing_clocks_stay_explicit():
    source = "|t:|100\n" + game() + "\n|t:|150"
    parser = ShowdownReplayParser()
    assert parser.parse(source).started_at == 100
    result = prepare(source, game("Bob", "Protect"))
    assert any("cannot be fully verified" in note for note in result.limitations)


@pytest.mark.parametrize("damage_line,reason", [
    ("|-damage|p2a: Incineroar|48/100", "visibility changed"),
    ("|-damage|p2a: Incineroar|111/120", "visibility changed"),
])
def test_changed_hp_denominator_is_excluded_instead_of_constraining_sets(damage_line, reason):
    source = log().replace("|-damage|p2a: Incineroar|48/110", damage_line)
    result = run(source, teamsheet=sheet())
    last = result.checkpoints[-1]
    assert not last.observation_ids
    assert any(reason in message for message in last.unsupported)


def test_increasing_hp_on_a_direct_damage_line_is_excluded():
    source = log().replace("|110/110", "|90/110").replace("|48/110", "|100/110")
    result = run(source, teamsheet=sheet())
    assert not result.checkpoints[-1].observation_ids
    assert any("HP increased" in message for message in result.checkpoints[-1].unsupported)


def test_zero_hp_attacker_does_not_create_false_inference_conflicts():
    source = log().replace("|100/100", "|0/100 fnt")
    result = run(source, teamsheet=sheet())
    last = result.checkpoints[-1]
    assert not last.observation_ids and last.outcome != "conflict"
    assert any("fainted attacker" in message for message in last.unsupported)


def test_heal_and_residual_update_next_hit_without_changing_prior_direct_damage():
    extra = "\n".join([
        "|-heal|p2a: Incineroar|80/110|[from] item: Sitrus Berry",
        "|-damage|p2a: Incineroar|74/110|[from] brn",
        "|turn|2", "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        "|-damage|p2a: Incineroar|12/110", "|win|Alice",
    ])
    parsed = ShowdownReplayParser().parse(log().replace("|win|Alice", extra))
    moves = [event for event in parsed.outcome.events if event.kind == "move"]
    assert [(event.hits[0].hp_before_raw, event.hits[0].hp_after_raw) for event in moves] == [
        ("110/110", "48/110"), ("74/110", "12/110")]


def test_pain_split_updates_both_hp_ledgers_before_the_next_attack():
    source = log().replace("|turn|1", "\n".join([
        "|-sethp|p1a: Garchomp|50/100|p2a: Incineroar|55/110|[from] move: Pain Split",
        "|turn|1",
    ]))
    state = ShowdownReplayParser().parse(source)
    move = next(event for event in state.outcome.events if event.kind == "move")
    assert move.state.mon("p1", "Garchomp").hp_raw == "50/100"
    assert move.hits[0].hp_before_raw == "55/110"


@pytest.mark.parametrize("source,original", [("ability: Trace", "Trace"), ("move: Skill Swap", None)])
def test_copied_ability_is_runtime_state_not_the_original_set(source, original):
    text = log().replace("|turn|1", f"|-ability|p1a: Garchomp|Intimidate|[from] {source}\n|turn|1")
    state = ShowdownReplayParser().parse(text)
    move = next(event for event in state.outcome.events if event.kind == "move")
    assert state.sides[0].team[0].ability == original
    assert move.state.mon("p1", "Garchomp").ability == "Intimidate"
    assert move.state.inference_exclusions


def test_intersecting_individually_legal_bounds_cannot_exceed_total_budget():
    prior = checkpoint()
    old = prior.summaries[0]
    previous = old.model_copy(update={"stat_points": {
        **old.stat_points, "hp": PointRange(minimum=32, maximum=32),
        "atk": PointRange(minimum=32, maximum=32)}, "basis": "necessary_local_bounds"})
    current = old.model_copy(update={"stat_points": {
        **old.stat_points, "def": PointRange(minimum=32, maximum=32)}})
    before = prior.model_copy(update={"summaries": (previous,)})
    after = prior.model_copy(update={"coverage": "bounded", "summaries": (current,)})
    result = retain_known_bounds(after, before)
    assert result.outcome == "conflict" and result.compatible_families == 0
    assert result.summaries == before.summaries and not result.joint_examples


def test_advice_is_labeled_hypothetical_without_mutating_evidence():
    evidence = {"improvement_suggestions": {"reason": "Improve special bulk"},
                "speed": {"faster_speed": 200}, "damage": 62}
    original = copy.deepcopy(evidence)
    text = coaching_context(evidence)
    assert text["suggestion_provenance"] == "hypothetical_advice_not_observed_registered_sets"
    assert "future_team_preparation" in text["investment_change_scope"]
    assert "faster_speed" not in text["speed"]
    assert text["damage"] == 62 and evidence == original


def test_ceiling_rounding_is_consistent_for_every_integer_hp_and_censored_ko():
    for maximum in (101, 137, 202):
        for hp in range(maximum + 1):
            percent = (hp * 100 + maximum - 1) // maximum
            assert displays(hp, maximum, HPReading(numerator=percent, denominator=100, mode="percent"))
            if percent:
                assert not displays(hp, maximum, HPReading(numerator=percent - 1, denominator=100, mode="percent"))


@pytest.mark.parametrize("damage,compatible", [(49, False), (50, True), (80, True)])
def test_ko_is_a_lower_damage_bound_not_an_exact_roll(damage, compatible):
    before = HPReading(numerator=50, denominator=100, mode="exact")
    observation = DamageObservation(id="ko", game=1, event_index=1, attacker_id="a", defender_id="b",
        request=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp"),
                            defender=PokemonSet(species="Incineroar"), move="Dragon Claw"),
        category="Physical", before=before, after=before.model_copy(update={"numerator": 0}),
        attacker_before=before)
    support = DamageSupport(supported=True, maximum_hp=100,
                            witnesses=(DamageWitness(hp_before=50, damage=damage),))
    assert agrees(observation, support) is compatible


def test_item_exchange_boosts_screens_and_weather_are_event_time_state():
    source = log().replace("|turn|1", "\n".join([
        "|-item|p1a: Garchomp|Life Orb", "|-item|p2a: Incineroar|Sitrus Berry", "|turn|1",
    ])).replace("|win|Alice", "\n".join([
        "|-enditem|p2a: Incineroar|Sitrus Berry",
        "|-enditem|p1a: Garchomp|Life Orb|[from] move: Trick",
        "|-item|p1a: Garchomp|Choice Band|[from] move: Trick",
        "|-boost|p1a: Garchomp|atk|2", "|-sidestart|p2: Bob|move: Reflect",
        "|-weather|RainDance", "|turn|2",
        "|move|p1a: Garchomp|Dragon Claw|p2a: Incineroar",
        "|-damage|p2a: Incineroar|1/110", "|win|Alice",
    ]))
    state = ShowdownReplayParser().parse(source)
    first, second = [event.state for event in state.outcome.events if event.kind == "move"]
    assert first.mon("p1", "Garchomp").item == "Life Orb"
    assert first.mon("p2", "Incineroar").item == "Sitrus Berry"
    assert not first.mon("p1", "Garchomp").boosts and not first.screens
    assert second.mon("p1", "Garchomp").item == "Choice Band"
    assert second.mon("p2", "Incineroar").item == ""
    assert second.mon("p1", "Garchomp").boosts == {"atk": 2}
    assert second.screens["p2"] == ["Reflect"]
    assert second.weather and second.weather != first.weather


def test_mirror_species_faint_on_opposite_sides_keeps_both_ko_records():
    source = "\n".join([
        "|player|p1|Alice|", "|player|p2|Bob|",
        "|switch|p1a: Cat|Incineroar, L50|10/100",
        "|switch|p2a: Cat|Incineroar, L50|10/100", "|turn|1",
        "|move|p1a: Cat|Flare Blitz|p2a: Cat",
        "|-damage|p2a: Cat|0 fnt", "|faint|p2a: Cat",
        "|-damage|p1a: Cat|0 fnt|[from] recoil", "|faint|p1a: Cat", "|tie|",
    ])
    outcome = ShowdownReplayParser().parse(source).outcome
    assert {ko.player for ko in outcome.kos} == {"p1", "p2"}
    assert any(event.kind == "faint" and event.actor_player == "p1" for event in outcome.events)
