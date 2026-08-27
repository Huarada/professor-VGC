"""Tests for the local-file-walk part of the Chaos-to-Firestore migration
script. The write path itself (batching/retry/sanitization) is shared with
sync_smogon_chaos_to_firestore.py and tested once in
test_chaos_firestore_writer.py.

No network/credentials needed — --dry-run never builds a Firestore client.
"""

from __future__ import annotations

from scripts.migrate_chaos_to_firestore import migrate


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
