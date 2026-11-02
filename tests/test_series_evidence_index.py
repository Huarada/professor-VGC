"""Series-local exact retrieval, causal cutoffs and multiple cross-game factors."""

import pytest

from src.domain.bo3_inference.evidence_index import SeriesEvidenceIndex
from src.domain.bo3_inference.models import EvidenceQuery
from src.domain.exceptions import SeriesValidationError
from src.services.bo3_inference.evidence_retrieval import damage_factors
from tests.test_boundary_search import setup


def sample(*, game=1, event=1, identifier=None):
    _, observation, _, _ = setup()
    return observation.model_copy(update={"game": game, "event_index": event,
        "id": identifier or f"g{game}:e{event}:p2:Incineroar"})


def index(scope="analysis-A"):
    return SeriesEvidenceIndex(scope=scope, pokemon_ids=("Alice:slot1", "Bob:slot1"))


def test_queries_are_exact_and_causal_across_games_and_rounds():
    memory = index()
    samples = [sample(game=3), sample(game=1, event=12), sample(game=2, event=4), sample(game=1, event=2)]
    memory.append(samples)
    found = memory.query(EvidenceQuery(through_game=2, through_event=3, pokemon_id="Bob:slot1", role="defender"))
    assert [(entry.game, entry.event_index) for entry in found] == [(1, 2), (1, 12)]
    found = memory.query(EvidenceQuery(through_game=2, pokemon_id="Alice:slot1", stat="atk", move="dragonclaw"))
    assert [(entry.game, entry.event_index) for entry in found] == [(1, 2), (1, 12), (2, 4)]
    assert not memory.query(EvidenceQuery(through_game=3, pokemon_id="Alice:slot1", stat="spd"))
    assert not memory.query(EvidenceQuery(through_game=3, pokemon_id="Alice:slot1", role="defender"))
    assert memory.retrievals[0].scope == "analysis-A"


def test_same_species_players_and_reused_session_names_never_share_evidence():
    first, second = index("same-session"), index("same-session")
    observation = sample()
    # Both opponents may use the same species. Their participant/slot IDs differ.
    request = observation.request.model_copy(update={"defender": observation.request.attacker})
    first.append([observation.model_copy(update={"request": request})])
    assert len(first.query(EvidenceQuery(through_game=1, pokemon_id="Alice:slot1", role="attacker"))) == 1
    assert not first.query(EvidenceQuery(through_game=1, pokemon_id="Bob:slot1", role="attacker"))
    assert not second.query(EvidenceQuery(through_game=3))
    with pytest.raises(SeriesValidationError, match="outside this series"):
        first.append([sample().model_copy(update={"attacker_id": "OtherPlayer:slot1"})])


def test_append_is_atomic_idempotent_and_preserves_runtime_state_against_mutation():
    memory = index()
    original = sample()
    original.request.attacker.boosts["atk"] = 1
    memory.append([original, original])
    original.request.attacker.boosts["atk"] = 6
    found = memory.query(EvidenceQuery(through_game=1))
    assert len(found) == 1 and found[0].request.attacker.boosts == {"atk": 1}
    found[0].request.attacker.boosts["atk"] = -6
    assert memory.query(EvidenceQuery(through_game=1))[0].request.attacker.boosts == {"atk": 1}
    with pytest.raises(SeriesValidationError, match="Conflicting"):
        memory.append([sample(game=2), original])
    assert len(memory.query(EvidenceQuery(through_game=3))) == 1


def test_alternate_stat_ownership_and_wonder_room_use_the_same_solver_dependencies():
    memory = index()
    foul_play = sample().model_copy(update={"attack_source": "defender", "attack_stat": "atk"})
    memory.append([foul_play])
    assert not memory.query(EvidenceQuery(through_game=1, pokemon_id="Alice:slot1", stat="atk"))
    assert memory.query(EvidenceQuery(through_game=1, pokemon_id="Bob:slot1", stat="atk"))
    press = sample(event=2).model_copy(update={"attack_stat": "def"})
    field = press.request.field.model_copy(update={"wonder_room": True})
    press = press.model_copy(update={"request": press.request.model_copy(update={"field": field})})
    memory.append([press])
    assert memory.query(EvidenceQuery(through_game=1, pokemon_id="Alice:slot1", stat="def"))[0].id == press.id
    assert memory.query(EvidenceQuery(through_game=1, pokemon_id="Bob:slot1", stat="spd"))[0].id == press.id


def test_planner_retrieves_multiple_pairs_and_three_game_factors_without_future_or_component_leaks():
    memory = index()
    observations = [sample(game=game, event=event) for game in range(1, 4) for event in (1, 2)]
    memory.append(observations + [sample(game=2, event=20)])
    factors = damage_factors(index=memory, observations=observations, game=2)
    pairs = {tuple(entry.id for entry in factor) for factor in factors if len(factor) == 2}
    assert (observations[1].id, observations[3].id) in pairs
    assert all(entry.game <= 2 and entry.event_index != 20 for factor in factors for entry in factor)
    final = damage_factors(index=memory, observations=observations, game=3)
    assert any(len(factor) == 3 and {entry.game for entry in factor} == {1, 2, 3} for factor in final)
    assert any(entry.query.stat == "def" for entry in memory.retrievals)
