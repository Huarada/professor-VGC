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
import os
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `src` imports

from src.adapters.chaos.chaos_tier_index import parse_tier_id
from src.adapters.chaos.species_normalize import normalize_species

_BATCH_LIMIT = 50
_COMMIT_MAX_ATTEMPTS = 6


def _strip_empty_keys(value: Any) -> Any:
    """Recursively drop any dict entry keyed by an empty string.

    Smogon's own Chaos export format universally includes a `"": <weight>`
    entry under every species' "Moves" map (observed in 100% of species
    checked in this project's real dumps) — Chaos's own convention for "the
    remaining probability mass with no confirmed 5th/6th move slot", not a
    real move. This is not a project-invented filter: `ChaosAdapter.
    _top_items`/`_summarize` already drop exactly this same falsy key when
    reading a LOCAL file (`if key` guards), so dropping it here too keeps
    Firestore-backed data consistent with how the local backend already
    treats it — the difference is that Firestore's field-path validation
    rejects an empty string as a map key outright (`ValueError: One or more
    components is not a string or is empty`), so this is not optional the
    way it was for the local backend, just newly enforced by the storage
    layer. Every other key, at every depth, is preserved byte-for-byte."""
    if isinstance(value, dict):
        return {
            key: _strip_empty_keys(val) for key, val in value.items() if key
        }
    if isinstance(value, list):
        return [_strip_empty_keys(item) for item in value]
    return value


def _iter_chaos_files(source: Path) -> Iterator[Path]:
    for path in sorted(source.glob("*.json")):
        yield path


def _new_batch_with(client: Any, ref: Any, data: dict[str, Any]) -> Any:
    batch = client.batch()
    batch.set(ref, data)
    return batch


def _commit_with_retry(batch: Any) -> None:
    """Commit a batch, retrying with exponential backoff on Firestore's
    transient ``ABORTED``/``Too much contention on these documents`` error —
    expected, documented Firestore behavior for a burst of writes into a
    brand-new collection (its automatic sharding hasn't warmed up yet), not
    a sign anything is actually wrong. Only ever hit during this one-time
    migration, never by the running app (which only ever reads)."""
    import time

    from google.api_core.exceptions import Aborted, ServiceUnavailable

    delay = 1.0
    for attempt in range(1, _COMMIT_MAX_ATTEMPTS + 1):
        try:
            batch.commit()
            return
        except (Aborted, ServiceUnavailable) as exc:
            if attempt == _COMMIT_MAX_ATTEMPTS:
                raise
            print(f"  [retry] batch commit contention (attempt {attempt}): {exc}"
                  f" — retrying in {delay:.1f}s")
            time.sleep(delay)
            delay *= 2


def migrate(
    *,
    project_id: str,
    source: Path,
    collection: str,
    database_id: str,
    credentials_path: str | None,
    ca_bundle_path: str | None,
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
        if ca_bundle_path:
            # See FirestoreChaosRepository._build_client's own comment —
            # same fix, same reason (grpc has its own TLS stack, unaffected
            # by pip-system-certs), needed here too since this script talks
            # to Firestore directly, not through that class.
            os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", ca_bundle_path)
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
        _commit_with_retry(_new_batch_with(client, tier_ref, {"info": _strip_empty_keys(info)}))

        batch = client.batch()
        pending = 0
        written = 0
        for species_name, mon_data in species_data.items():
            doc_ref = tier_ref.collection("species").document(normalize_species(species_name))
            clean_mon_data = _strip_empty_keys(mon_data)
            batch.set(doc_ref, {**clean_mon_data, "original_name": species_name})
            pending += 1
            if pending >= _BATCH_LIMIT:
                _commit_with_retry(batch)
                written += pending
                print(f"  ... {written}/{len(species_data)} species written")
                batch = client.batch()
                pending = 0
        if pending:
            _commit_with_retry(batch)
            written += pending
        print(f"  done: {written}/{len(species_data)} species written")

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
    parser.add_argument(
        "--ca-bundle", default=None,
        help="a CA bundle (PEM) for grpc to trust in addition to its own roots — only "
             "needed if something on this machine TLS-intercepts outbound HTTPS with a "
             "locally-installed root cert (a security suite, a corporate proxy, ...); "
             "see DATA.md's Firestore section",
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
        ca_bundle_path=args.ca_bundle,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
