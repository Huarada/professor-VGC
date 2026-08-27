"""Fetch the latest month's current-gen VGC Chaos stats directly from
Smogon (https://www.smogon.com/stats/) and sync them into Firestore.

    python -m scripts.sync_smogon_chaos_to_firestore --project-id YOUR_PROJECT \
        [--month 2026-07] [--credentials path/to/key.json] [--dry-run]

Designed to run as a **scheduled Cloud Run Job** (Cloud Scheduler -> Cloud
Run Job, no Pub/Sub — see DATA.md's "Automated Smogon sync" section for why,
and for the full deploy runbook), and equally runnable locally for testing.
Idempotent: re-running simply overwrites each tier's species documents with
Smogon's latest published numbers for that SAME tier id, exactly like a
manual re-run of migrate_chaos_to_firestore.py would — there is no
"history" concept here (a deliberate choice: this project's regulation
fallback already covers "old data", and Smogon's own site doesn't keep a
changelog we could diff against anyway).

**Scope: current-generation VGC formats only.** Smogon publishes stats for
~150 other formats a month (every gen's OU, Ubers, Randoms, Monotype, ...)
this project has no use for and that would blow well past Firestore's free
tier for zero product benefit — see `_is_current_gen_vgc`.

Every actual Firestore write (batching, retry-on-contention, the
empty-string-key sanitizer) is shared with `migrate_chaos_to_firestore.py`
via `chaos_firestore_writer.py`, not reimplemented here — this module's own
job is only "get the right bytes from Smogon."
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `src`/`scripts` imports

from scripts.chaos_firestore_writer import build_firestore_client, write_tier

_STATS_ROOT = "https://www.smogon.com/stats/"
_MONTH_RE = re.compile(r'href="(\d{4}-\d{2})/"')
_FILE_RE = re.compile(r'href="([^"]+\.json\.gz)"')
# Identifies this job to Smogon's server logs, per common web-scraping
# etiquette — a default urllib User-Agent gets blocked by some servers, and
# an honest one makes it easy for Smogon to see who's hitting their site if
# it's ever a problem.
_USER_AGENT = "ProfessorVGC-chaos-sync/1.0 (+https://github.com/Huarada/professor-VGC)"
_FETCH_TIMEOUT_SECONDS = 30.0


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            return response.read()  # type: ignore[no-any-return]
    except urllib.error.URLError as exc:
        raise SystemExit(f"Unable to fetch {url}: {exc}") from exc


def _latest_month() -> str:
    html = _fetch(_STATS_ROOT).decode("utf-8", errors="replace")
    months = _MONTH_RE.findall(html)
    if not months:
        raise SystemExit(f"No month directories found at {_STATS_ROOT}")
    return max(months)  # "YYYY-MM" sorts correctly as a plain string


def _is_current_gen_vgc(filename: str) -> bool:
    """Current-generation VGC formats only — matches this project's own
    scope (CLAUDE.md: VGC-only; PROFESSORVGC_CALC_GEN=9).

    Deliberately a loose "starts with gen9 and mentions vgc" check, not a
    strict regulation-name regex: Smogon adds format variants (a Bo3 split
    alongside the regular Bo1 format, a new regulation letter every couple
    months, ...) whose names this project's own
    chaos_tier_index.parse_tier_id doesn't always fully parse into
    (gen, franchise, year, reg) — those still degrade gracefully to a
    standalone, independently-queryable tier there (see its own docstring),
    just outside the regulation-fallback chain for the "main" line, which is
    the right degrade for something that's a parallel variant, not an older
    regulation. Being permissive HERE and precise on the READ side is the
    right split of responsibility — this filter never needs updating as
    Smogon's own naming conventions shift.
    """
    name = filename.removesuffix(".json.gz")
    return name.startswith("gen9") and "vgc" in name


def _list_vgc_files(month: str) -> list[str]:
    url = f"{_STATS_ROOT}{month}/chaos/"
    html = _fetch(url).decode("utf-8", errors="replace")
    return sorted(f for f in _FILE_RE.findall(html) if _is_current_gen_vgc(f))


def sync(
    *,
    project_id: str,
    month: str | None,
    collection: str,
    database_id: str,
    credentials_path: str | None,
    ca_bundle_path: str | None,
    dry_run: bool,
) -> None:
    resolved_month = month or _latest_month()
    print(f"Smogon stats month: {resolved_month}")

    filenames = _list_vgc_files(resolved_month)
    if not filenames:
        raise SystemExit(
            f"No current-gen VGC files found for {resolved_month} at "
            f"{_STATS_ROOT}{resolved_month}/chaos/ — check the month actually exists."
        )
    print(f"Found {len(filenames)} current-gen VGC file(s): {', '.join(filenames)}")

    client: Any = None
    if not dry_run:
        client = build_firestore_client(project_id, database_id, credentials_path, ca_bundle_path)

    total_species = 0
    for filename in filenames:
        tier_id = filename.removesuffix(".json.gz")
        url = f"{_STATS_ROOT}{resolved_month}/chaos/{filename}"
        print(f"=== {tier_id} ===")
        raw = _fetch(url)
        payload = json.loads(gzip.decompress(raw))
        info = payload.get("info") or {}
        species_data: dict[str, Any] = payload.get("data") or {}
        print(f"  downloaded ({len(raw)} bytes gzipped, {len(species_data)} species)")
        if dry_run:
            total_species += len(species_data)
            continue
        total_species += write_tier(client, collection, tier_id, info, species_data)

    verb = "Would sync" if dry_run else "Synced"
    print(
        f"\n{verb} {len(filenames)} tier(s), {total_species} species document(s) "
        f"from Smogon {resolved_month} to Firestore collection '{collection}' "
        f"(project={project_id})."
    )
    if client is not None:
        client.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Every flag also reads a PROFESSORVGC_*-prefixed env var default, so the
    # SAME container image works unchanged as a Cloud Run Job configured
    # entirely via --set-env-vars, with no per-invocation args needed.
    parser.add_argument(
        "--project-id", default=os.environ.get("PROFESSORVGC_FIRESTORE_PROJECT_ID", ""),
        help="GCP project id (default: $PROFESSORVGC_FIRESTORE_PROJECT_ID)",
    )
    parser.add_argument(
        "--month", default=os.environ.get("PROFESSORVGC_SYNC_MONTH") or None,
        help="YYYY-MM (default: $PROFESSORVGC_SYNC_MONTH, else the latest month Smogon has published)",
    )
    parser.add_argument(
        "--credentials", default=os.environ.get("PROFESSORVGC_FIRESTORE_CREDENTIALS_PATH") or None,
        help="service account JSON key path (omit for Application Default Credentials — "
             "the normal case inside Cloud Run, via its attached runtime service account)",
    )
    parser.add_argument(
        "--ca-bundle", default=os.environ.get("PROFESSORVGC_FIRESTORE_GRPC_CA_BUNDLE_PATH") or None,
        help="local-machine-only TLS workaround — see migrate_chaos_to_firestore.py's own flag; "
             "never needed inside Cloud Run itself",
    )
    parser.add_argument(
        "--collection",
        default=os.environ.get("PROFESSORVGC_FIRESTORE_CHAOS_COLLECTION", "chaos_tiers"),
    )
    parser.add_argument(
        "--database", default=os.environ.get("PROFESSORVGC_FIRESTORE_DATABASE_ID", "(default)")
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list + download + count only — no Firestore connection, no writes",
    )
    args = parser.parse_args()

    if not args.project_id:
        raise SystemExit(
            "A GCP project id is required: --project-id or $PROFESSORVGC_FIRESTORE_PROJECT_ID"
        )

    sync(
        project_id=args.project_id,
        month=args.month,
        collection=args.collection,
        database_id=args.database,
        credentials_path=args.credentials,
        ca_bundle_path=args.ca_bundle,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
