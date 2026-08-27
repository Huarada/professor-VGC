"""One-time migration: upload local Chaos JSON files into Firestore.

    python -m scripts.migrate_chaos_to_firestore --project-id YOUR_PROJECT \
        [--credentials path/to/key.json] [--source data/chaos] \
        [--collection chaos_tiers] [--database "(default)"] [--dry-run]

Reads every ``<metagame>-<cutoff>.json`` Chaos file under ``--source``
(read-only — NEVER modifies them, per CLAUDE.md's absolute rule against
editing anything under ``data/chaos/``) and writes, per file:

    {collection}/{tier_id}                                    (1 doc, tiny)
        info: <the file's original "info" object>
    {collection}/{tier_id}/species/{normalized_species_id}     (1 doc per species)
        <the species' original Chaos JSON object, verbatim>
        original_name: <species>

``tier_id`` is the filename with ``.json`` stripped (e.g.
``gen9championsvgc2026regmb-1760``) — read back by
``FirestoreChaosRepository`` via the exact same ``parse_tier_id`` used here,
so the two directions of this migration can never drift apart on how a tier
id is interpreted. Species documents are keyed by ``normalize_species()``
(also shared with the read side), never by the raw display name — a lookup
at query time is then always a single direct document read, never a
collection scan.

Batched (Firestore's own 500-writes-per-batch limit) so a full 4-tier,
~1,100-species migration finishes in a handful of batches — comfortably
inside the free tier's 20k-writes/day quota. This is a ONE-TIME cost; the
running app only ever reads afterward (one document read per species
actually needed for an analysis, not per tier).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `src` imports

from src.adapters.chaos.chaos_tier_index import parse_tier_id
from src.adapters.chaos.species_normalize import normalize_species

_BATCH_LIMIT = 500


def _iter_chaos_files(source: Path) -> Iterator[Path]:
    for path in sorted(source.glob("*.json")):
        yield path


def migrate(
    *,
    project_id: str,
    source: Path,
    collection: str,
    database_id: str,
    credentials_path: str | None,
    dry_run: bool,
) -> None:
    client: Any = None
    if not dry_run:
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover - env dependent
            raise SystemExit(
                "The 'google-cloud-firestore' package is not installed. "
                "Run: pip install google-cloud-firestore"
            ) from exc
        if credentials_path:
            from google.oauth2.service_account import Credentials

            credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_path
            )
            client = firestore.Client(
                project=project_id, database=database_id, credentials=credentials
            )
        else:
            client = firestore.Client(project=project_id, database=database_id)

    files = list(_iter_chaos_files(source))
    if not files:
        raise SystemExit(f"No Chaos files found under {source}")

    total_tiers = 0
    total_species = 0
    for path in files:
        tier_id = path.stem
        meta = parse_tier_id(tier_id)
        if meta is None:
            print(f"  [skip] {path.name}: unrecognized filename shape")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        info = payload.get("info") or {}
        species_data: dict[str, Any] = payload.get("data") or {}

        print(f"=== {tier_id} ({len(species_data)} species) ===")
        total_tiers += 1
        total_species += len(species_data)
        if dry_run:
            continue

        tier_ref = client.collection(collection).document(tier_id)
        batch = client.batch()
        batch.set(tier_ref, {"info": info})
        pending = 1
        for species_name, mon_data in species_data.items():
            doc_ref = tier_ref.collection("species").document(normalize_species(species_name))
            batch.set(doc_ref, {**mon_data, "original_name": species_name})
            pending += 1
            if pending >= _BATCH_LIMIT:
                batch.commit()
                batch = client.batch()
                pending = 0
        if pending:
            batch.commit()

    verb = "Would write" if dry_run else "Wrote"
    print(
        f"\n{verb} {total_tiers} tier(s), {total_species} species document(s) "
        f"to Firestore collection '{collection}' (project={project_id})."
    )
    if client is not None:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", required=True, help="GCP project id")
    parser.add_argument(
        "--credentials", default=None,
        help="path to a service account JSON key (omit to use Application Default Credentials)",
    )
    parser.add_argument("--source", default="data/chaos", help="directory of local Chaos *.json files")
    parser.add_argument("--collection", default="chaos_tiers", help="Firestore root collection name")
    parser.add_argument("--database", default="(default)", help="Firestore database id")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="parse and count only — no Firestore connection, no writes",
    )
    args = parser.parse_args()

    migrate(
        project_id=args.project_id,
        source=Path(args.source),
        collection=args.collection,
        database_id=args.database,
        credentials_path=args.credentials,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
