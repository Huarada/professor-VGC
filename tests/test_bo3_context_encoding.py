"""Independent expansion oracle for lossless, bounded BO3 narrator inputs."""

import copy
import json

from src.services.bo3_context_encoding import share_evidence
from src.services.bo3_prompt_context import (
    CONTEXT_BYTES,
    compact_bo3_context,
    encode_context,
)


def expand_evidence(packed):
    """Decode using only the documented wire format, independently of the encoder."""
    encoding = packed.get("bo3_evidence_encoding")
    if encoding is None:
        return packed
    values = encoding["shared_values"]
    active = set()

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"bo3_value_ref"}:
                key = value["bo3_value_ref"]
                assert key not in active, "References must never cycle"
                active.add(key)
                result = expand(values[key])
                active.remove(key)
                return result
            if set(value) == {"bo3_table", "rows"}:
                columns = encoding["columns"][value["bo3_table"]]
                assert all(len(row) == len(columns) for row in value["rows"])
                return [dict(zip(columns, [expand(entry) for entry in row])) for row in value["rows"]]
            return {key: expand(entry) for key, entry in value.items()}
        if isinstance(value, list):
            return [expand(entry) for entry in value]
        return value

    return expand({key: value for key, value in packed.items() if key != "bo3_evidence_encoding"})


def evidence():
    caveat = "Calculated damage uses assumed sets, not recovered original stats. " * 5
    return {"context_compaction": {"full_calculated_evidence_retained_in_results": True},
        "game_evidence": [{"game": game, "turn_checks": [
            {"turn": turn, "actor": "Gengar-Mega", "actor_player": "p1" if game != 2 else "p2",
             "move": "Sludge Bomb", "conditions": ["Light Screen"], "note": caveat,
             "damage_checks": [{"target": "Grimmsnarl", "before": 100, "after": 42,
                                "projected": [58.0, 70.0], "ko": False, "unknown": None}],
             "traits": {"nature": "Modest", "item": "Gengarite", "ability": "Shadow Tag"}}
            for turn in range(1, 121)]} for game in range(1, 4)],
        "trusted_stat_spread_findings": [{"game": 3, "partial_local_constraints": 1,
            "pokemon": [{"pokemon_id": "Alice:slot4", "constrained_native_bounds": {"def": {"minimum": 0, "maximum": 23}},
                         "complete_spread_known": False}]}]}


def test_large_three_game_context_fits_without_losing_a_single_supplied_value():
    source = evidence()
    original = copy.deepcopy(source)
    assert len(encode_context(source).encode("utf-8")) > CONTEXT_BYTES * 2
    packed = compact_bo3_context(source)
    assert len(encode_context(packed).encode("utf-8")) < CONTEXT_BYTES
    assert expand_evidence(packed) == original
    assert source == original and compact_bo3_context(packed) == packed
    # Repeated values can be shared without merging game order or swapped sides.
    expanded = expand_evidence(packed)
    assert [game["turn_checks"][0]["actor_player"] for game in expanded["game_evidence"]] == ["p1", "p2", "p1"]


def test_lossless_encoding_preserves_missing_null_empty_zero_false_and_unicode():
    source = {"nested": [{"unknown": None, "empty": "", "list": [], "zero": 0, "flag": False,
                          "text": "Pokémon — 属性 " * 40},
                         {"different_keys": True}],
              "copies": [{"explanation": "é" * 180, "points": {"minimum": 0, "maximum": 23}}] * 40}
    original = copy.deepcopy(source)
    packed = share_evidence(source)
    assert "bo3_evidence_encoding" in packed
    assert expand_evidence(packed) == original and source == original
    # Every shared-value reference targets a previously defined value.
    defined = set()
    for key, value in packed["bo3_evidence_encoding"]["shared_values"].items():
        def check(entry):
            if isinstance(entry, dict):
                if set(entry) == {"bo3_value_ref"}:
                    assert entry["bo3_value_ref"] in defined
                else:
                    for child in entry.values():
                        check(child)
            elif isinstance(entry, list):
                for child in entry:
                    check(child)
        check(value)
        defined.add(key)


def test_unique_small_payload_keeps_its_readable_shape():
    source = {"turn": 1, "damage": [0, 20], "nature": None}
    assert share_evidence(source) == source


def test_encoded_context_survives_json_transport():
    source = evidence()
    wire = json.loads(encode_context(share_evidence(source)))
    assert expand_evidence(wire) == source


def test_parent_compaction_rebases_encoded_children_without_reference_collisions():
    first = evidence()
    second = evidence()
    second["game_evidence"][0]["turn_checks"][0]["conditions"] = ["Rain"]
    source = {"context_compaction": {}, "games": [share_evidence(first), share_evidence(second)],
              "additional_evidence": evidence()}
    packed = share_evidence(source)
    assert expand_evidence(packed) == {"context_compaction": {}, "games": [first, second],
                                       "additional_evidence": evidence()}
