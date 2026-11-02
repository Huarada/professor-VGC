"""Lossless shared-value encoding for large BO3 explanation payloads."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _expand_nested(value: Any, registry: dict[str, Any] | None = None) -> Any:
    """Unify previously encoded child contexts before assigning new root IDs."""
    if isinstance(value, dict):
        if "bo3_evidence_encoding" in value:
            registry = value["bo3_evidence_encoding"]
            value = {key: entry for key, entry in value.items() if key != "bo3_evidence_encoding"}
        if registry is not None:
            if set(value) == {"bo3_value_ref"}:
                return _expand_nested(registry["shared_values"][value["bo3_value_ref"]], registry)
            if set(value) == {"bo3_table", "rows"}:
                columns = registry["columns"][value["bo3_table"]]
                return [{key: _expand_nested(entry, registry) for key, entry in zip(columns, row)}
                        for row in value["rows"]]
        return {key: _expand_nested(entry, registry) for key, entry in value.items()}
    if isinstance(value, list):
        return [_expand_nested(entry, registry) for entry in value]
    return value


def share_evidence(context: dict[str, Any]) -> dict[str, Any]:
    """Replace repeated JSON values with backward-only, root-scoped references.

    Every row stays in its original game and chronological position. References
    carry no inferred meaning: expanding them reconstructs the supplied payload.
    The dictionaries themselves are encoded bottom-up, so they cannot cycle.
    """
    original_size = len(_encode(context).encode("utf-8"))
    expanded = _expand_nested(context)
    counts: Counter[str] = Counter()

    def count(value: Any) -> None:
        if isinstance(value, (dict, list, str)):
            encoded = _encode(value)
            if len(encoded.encode("utf-8")) >= 120:
                counts[encoded] += 1
        if isinstance(value, dict):
            for entry in value.values():
                count(entry)
        elif isinstance(value, list):
            for entry in value:
                count(entry)

    count(expanded)
    shared: dict[str, Any] = {}
    references: dict[str, str] = {}
    schemas: dict[str, list[str]] = {}
    schema_ids: dict[tuple[str, ...], str] = {}

    def pack(value: Any) -> Any:
        encoded = _encode(value) if isinstance(value, (dict, list, str)) else ""
        repeated = counts[encoded] > 1
        if repeated and encoded in references:
            return {"bo3_value_ref": references[encoded]}
        if isinstance(value, dict):
            packed: Any = {key: pack(entry) for key, entry in value.items()}
        elif isinstance(value, list):
            # Equal ordered keys preserve missing-vs-null as well as empty values.
            if value and all(isinstance(row, dict) for row in value):
                columns = tuple(value[0])
                if columns and all(tuple(row) == columns for row in value):
                    schema = schema_ids.get(columns)
                    if schema is None:
                        schema = f"columns_{len(schemas)}"
                        schema_ids[columns] = schema
                        schemas[schema] = list(columns)
                    packed = {"bo3_table": schema,
                              "rows": [[pack(row[column]) for column in columns] for row in value]}
                else:
                    packed = [pack(entry) for entry in value]
            else:
                packed = [pack(entry) for entry in value]
        else:
            packed = value
        if repeated:
            reference = f"value_{len(shared)}"
            references[encoded] = reference
            shared[reference] = packed
            return {"bo3_value_ref": reference}
        return packed

    packed = pack(expanded)
    result = dict(packed)
    result["bo3_evidence_encoding"] = {
        "version": 1, "columns": schemas, "shared_values": shared,
        "meaning": "Lossless root-scoped encoding; expand references and table columns before interpreting evidence.",
    }
    # Some small/unique payloads gain no space; leave their existing shape intact.
    return result if len(_encode(result).encode("utf-8")) < original_size else context
