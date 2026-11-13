"""Bound BO3 narrator inputs without changing calculated evidence or replay order."""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from src.domain.exceptions import LLMProviderError
from src.services.bo3_context_encoding import share_evidence

CONTEXT_BYTES = 100_000
MESSAGE_BYTES = 110_000
DAMAGE_COLUMNS = ("target", "target_player", "target_hp_before_percent", "projected_min_percent",
                  "projected_max_percent", "projected_ko_text", "actual_result", "actual_hp_remaining_percent")


def encode_context(value: Any) -> str:
    """Compact JSON keeps data intact; whitespace is not coaching evidence."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def checked_bo3_message(text: str) -> str:
    """Fail locally rather than pay for an overlarge provider request."""
    size = len(text.encode("utf-8"))
    if size > MESSAGE_BYTES:
        raise LLMProviderError(
            f"The BO3 explanation input is too large ({size:,} bytes; limit {MESSAGE_BYTES:,}). "
            "Use a shorter question. This is an application context limit, not an API quota error."
        )
    return text


def _checks(value: Any) -> Any:
    if isinstance(value, dict):
        if "projected_min_percent" in value and "actual_result" in value:
            return [value.get(column) for column in DAMAGE_COLUMNS]
        return {key: _checks(entry) for key, entry in value.items() if key not in {"description", "speed"}}
    if isinstance(value, list):
        return [_checks(entry) for entry in value]
    return value


def _inference(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: entry for key, entry in value.items()
              if key not in {"checkpoints", "conditioned_projections", "evidence_retrievals", "damage_citations"}}
    checkpoints = []
    for checkpoint in value.get("checkpoints", []):
        entry = {key: item for key, item in checkpoint.items()
                 if key not in {"summaries", "joint_examples", "unsupported", "observation_ids", "local_constraints",
                                "partial_local_constraints"}}
        entry["eligible_damage_observations"] = len(checkpoint.get("observation_ids", []))
        entry["complete_local_constraints"] = len(checkpoint.get("local_constraints", []))
        entry["partial_local_constraints"] = len(checkpoint.get("partial_local_constraints", []))
        reasons = Counter(reason.split(": ", 1)[-1] for reason in checkpoint.get("unsupported", []))
        entry["unsupported_reason_counts"] = dict(reasons)
        checkpoints.append(entry)
    if "checkpoints" in value:
        result["checkpoints"] = checkpoints
    result["conditioned_damage_examples_in_full_results"] = len(value.get("conditioned_projections", []))
    return result


def _findings(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous: dict[str, tuple[str, str]] = {}
    result = []
    for checkpoint in checkpoints:
        entry = {key: value for key, value in checkpoint.items() if key != "pokemon"}
        identifier = f"g{checkpoint['game']}:{checkpoint['phase']}"
        entry["checkpoint_id"] = identifier
        pokemon = []
        for original in checkpoint.get("pokemon", []):
            mon = copy.deepcopy(original)
            mon["range_reductions"] = {stat: change for stat, change in mon.get("range_reductions", {}).items()
                                       if change["removed_integer_values"] > 0}
            for form in mon.get("forms", []):
                form["range_reductions"] = {stat: change for stat, change in form.get("range_reductions", {}).items()
                                            if change["removed_integer_values"] > 0}
                if not mon.get("complete_spread_known"):
                    attributes = form.get("unboosted_attributes", {})
                    informative = set(form["range_reductions"]) | set(mon.get("declared_native_bounds", {}))
                    form["uninformative_attribute_ranges_in_full_tables"] = [stat for stat in attributes if stat not in informative]
                    form["unboosted_attributes"] = {stat: bounds for stat, bounds in attributes.items() if stat in informative}
            key = mon["pokemon_id"]
            serialized = encode_context(mon)
            old = previous.get(key)
            if old and old[0] == serialized:
                pokemon.append({"pokemon_id": key, "species": mon["species"], "unchanged_from_checkpoint": old[1]})
            else:
                pokemon.append(mon)
                previous[key] = (serialized, identifier)
        entry["pokemon"] = pokemon
        result.append(entry)
    return result


def _pack_turns(context: dict[str, Any]) -> dict[str, Any]:
    """Column headers encode repeated field names once, retaining every row/value."""
    result = copy.deepcopy(context)
    for key in ("turn_checks", "turn_by_turn_checks"):
        checks = result.get(key, [])
        if checks and all(isinstance(check, dict) for check in checks):
            columns = list(dict.fromkeys(column for check in checks for column in check))
            result["context_compaction"].setdefault("turn_check_columns", {})[key] = columns
            result[key] = [[check.get(column) for column in columns] for check in checks]
    counts: Counter[str] = Counter()

    def count(value: Any) -> None:
        if isinstance(value, str) and len(value) > 100:
            counts[value] += 1
        elif isinstance(value, list):
            for entry in value:
                count(entry)
        elif isinstance(value, dict):
            for entry in value.values():
                count(entry)

    keys = ("turn_checks", "turn_by_turn_checks", "protect_reads")
    for key in keys:
        count(result.get(key))
    shared = {value: f"text_{index}" for index, (value, total) in enumerate(counts.items()) if total > 1}

    def replace(value: Any) -> Any:
        if isinstance(value, str) and value in shared:
            return {"shared_check_text_ref": shared[value]}
        if isinstance(value, dict):
            return {key: replace(entry) for key, entry in value.items()}
        if isinstance(value, list):
            return [replace(entry) for entry in value]
        return value

    if shared:
        result["shared_check_text"] = {reference: text for text, reference in shared.items()}
        for key in keys:
            if key in result:
                result[key] = replace(result[key])
    return result


def _fit_context(context: dict[str, Any]) -> dict[str, Any]:
    size = len(encode_context(context).encode("utf-8"))
    # Verified claim catalogs are additional trusted evidence. Share repeated
    # structure earlier rather than consuming the former overflow headroom.
    target = min(CONTEXT_BYTES, 70_000) if "verified_claim_plan" in context else CONTEXT_BYTES
    if size <= target:
        return context
    packed = share_evidence(context) if "bo3_evidence_encoding" not in context else context
    size = len(encode_context(packed).encode("utf-8"))
    if size > CONTEXT_BYTES:
        raise LLMProviderError(
            f"The BO3 evidence exceeds the explanation context budget after compaction "
            f"({size:,} bytes; limit {CONTEXT_BYTES:,}). This is an application context limit, not an API quota error; "
            "chronological evidence was not truncated."
        )
    return packed


def compact_bo3_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep chronological facts and trusted bounds; omit duplicated diagnostics."""
    result = copy.deepcopy(context)
    if "context_compaction" in result:
        return _fit_context(result)
    if isinstance(result.get("set_inference"), dict):
        result["set_inference"] = _inference(result["set_inference"])
    findings = result.get("trusted_stat_spread_findings")
    if isinstance(findings, list):
        result["trusted_stat_spread_findings"] = _findings(findings)
    if isinstance(result.get("series_context"), dict):
        result["series_context"] = compact_bo3_context(result["series_context"])
    for key in ("turn_checks", "turn_by_turn_checks", "protect_reads"):
        if key in result:
            result[key] = _checks(result[key])
    if "game_evidence" in result:
        # Metagame profiles and strategy texts already served the individual
        # reviews. Synthesis uses ordered mechanical evidence, never model prose.
        games = []
        for game in result["game_evidence"]:
            entry = {key: value for key, value in game.items()
                     if key not in {"meta_context", "strategies", "verdicts", "selection"}}
            for check in entry.get("turn_checks", []):
                for key in ("best_alternatives", "incoming_threats", "decision_options"):
                    if check.get(key):
                        values = check.pop(key)
                        check[key + "_in_individual_game_review"] = len(values)
                        if key != "best_alternatives":
                            check[key] = values[:1]
            games.append(compact_bo3_context(entry))
        result["game_evidence"] = games
    result["context_compaction"] = {
        "full_calculated_evidence_retained_in_results": True,
        "calculator_descriptions_omitted": True,
        "unchanged_findings_reference_earlier_checkpoints_only": True,
        "zero_reduction_details_omitted": True,
        "assumed_set_speed_is_not_an_observed_stat": True,
        "damage_check_columns": list(DAMAGE_COLUMNS),
        "projected_damage_is_conditioned_on_assumed_sets": True,
    }
    packed = _pack_turns(result)
    if len(encode_context(packed).encode("utf-8")) <= CONTEXT_BYTES:
        return _fit_context(packed)
    if len(encode_context(packed).encode("utf-8")) > CONTEXT_BYTES:
        for key in ("meta_context", "strategies", "deterministic_verdicts", "improvement_suggestions", "recurring_concepts"):
            if key in result:
                result.pop(key)
                result["context_compaction"].setdefault("optional_sections_omitted", []).append(key)
        games = result.get("game_evidence", [result])
        for game in games:
            for check in game.get("turn_checks", game.get("turn_by_turn_checks", [])):
                if not isinstance(check, dict):
                    continue
                for key in ("best_alternatives", "incoming_threats", "decision_options"):
                    if len(check.get(key, [])) > 1:
                        check[key + "_omitted_count"] = len(check[key]) - 1
                        check[key] = check[key][:1]
        result["context_compaction"]["optional_alternatives_limited_to_first_listed"] = True
    packed = _pack_turns(result)
    return _fit_context(packed)
