"""Tests for the local-only, LLM-free parts of the Chaos-to-Firestore
migration script: the empty-key sanitizer and the dry-run file walk.

No network/credentials needed — --dry-run never builds a Firestore client at
all (see migrate()), and _strip_empty_keys is pure data transformation.
"""

from __future__ import annotations

from scripts.migrate_chaos_to_firestore import _strip_empty_keys, migrate


def test_strip_empty_keys_drops_empty_string_keys_at_every_depth():
    """Smogon's own Chaos export universally includes a `"": <weight>` entry
    under every species' "Moves" map — Firestore's field-path validation
    rejects an empty string as a map key outright, so this is load-bearing,
    not cosmetic (reported: the real migration crashed on this before the
    fix, mid-way through the first tier)."""
    raw = {
        "Moves": {"": 5.2, "Earthquake": 90.1},
        "Checks and Counters": {"": {"p": 0.1}, "Ting-Lu": {"p": 0.6}},
        "nested_list": [{"": 1, "keep": 2}, {"also_keep": 3}],
        "Raw count": 100,
    }
    cleaned = _strip_empty_keys(raw)
    assert cleaned == {
        "Moves": {"Earthquake": 90.1},
        "Checks and Counters": {"Ting-Lu": {"p": 0.6}},
        "nested_list": [{"keep": 2}, {"also_keep": 3}],
        "Raw count": 100,
    }


def test_strip_empty_keys_preserves_every_real_key_and_value_unchanged():
    """Every non-empty key, at any depth, must survive byte-for-byte — the
    whole point of this migration is storing the data "as-is", not
    reshaping it."""
    raw = {"Abilities": {"Levitate": 100}, "Items": {"Leftovers": 50}, "Spreads": {"Modest:0/0/0/32/0/32": 40}}
    assert _strip_empty_keys(raw) == raw


def test_strip_empty_keys_is_a_noop_on_non_dict_non_list_values():
    assert _strip_empty_keys("Garchomp") == "Garchomp"
    assert _strip_empty_keys(42) == 42
    assert _strip_empty_keys(None) is None


def test_dry_run_counts_real_chaos_files_without_any_firestore_client(tmp_path, capsys):
    """--dry-run must never import/construct a Firestore client — verified
    here by never passing real project credentials at all and confirming it
    still completes successfully, counting fixture files."""
    (tmp_path / "gen9vgc2025regh-0.json").write_text(
        '{"info": {"metagame": "gen9vgc2025regh"}, "data": '
        '{"Garchomp": {"Moves": {"": 1, "Earthquake": 90}}}}',
        encoding="utf-8",
    )
    migrate(
        project_id="unused", source=tmp_path, collection="chaos_tiers",
        database_id="(default)", credentials_path=None, ca_bundle_path=None,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert "gen9vgc2025regh-0 (1 species)" in out
    assert "Would write 1 tier(s), 1 species document(s)" in out
