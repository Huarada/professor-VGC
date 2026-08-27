"""Shared Firestore write path for Chaos tier data.

Used by both ``migrate_chaos_to_firestore.py`` (local ``data/chaos/*.json``
files, a manual/dev tool) and ``sync_smogon_chaos_to_firestore.py`` (fetches
directly from Smogon's own stats site, meant to run on a schedule) so the
two entrypoints can never drift on batching, retry, or sanitization behavior
— both write the exact same document shape
(``FirestoreChaosRepository``/``DATA.md`` describe it) regardless of where
the source bytes came from.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.cloud.firestore import Client

_BATCH_COUNT_LIMIT = 50
# A first-pass sizing heuristic to avoid the DEGENERATE case (one huge batch
# that then has to split itself many times over) — NOT relied on as the
# actual safety guarantee, because Firestore's real "too big" limit counts
# more than raw document bytes (per Google's own docs: "transaction size
# depends on the sizes of documents AND INDEX ENTRIES modified" — every
# field of every document is auto-indexed by default, and this collection's
# documents are wide maps with many distinct keys (Moves/Items/Teammates/
# Checks-and-Counters), so the real per-document write cost runs well above
# its own JSON byte count in a way that isn't practical to predict exactly
# from Python). Reported live: even a conservative 3 MiB raw-JSON cap still
# hit "400 Transaction too big" on Smogon's July 2026 dump. See
# _commit_pairs' adaptive splitting below for the actual guarantee, and
# DATA.md's Firestore section for the real fix (disabling automatic
# indexing on this collection, since every read here is a direct
# document-id lookup — never a query — so the index is pure waste).
_BATCH_BYTES_LIMIT = 1 * 1024 * 1024
_COMMIT_MAX_ATTEMPTS = 6


def strip_empty_keys(value: Any) -> Any:
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
        return {key: strip_empty_keys(val) for key, val in value.items() if key}
    if isinstance(value, list):
        return [strip_empty_keys(item) for item in value]
    return value


def build_firestore_client(
    project_id: str,
    database_id: str,
    credentials_path: str | None,
    ca_bundle_path: str | None,
) -> "Client":
    """BYOK-style factory: an explicit service-account key file for local
    dev/testing, else Application Default Credentials — which resolves
    automatically both for an operator who ran `gcloud auth
    application-default login` AND for a Cloud Run Job's own attached
    runtime service account (the metadata server), with no "am I running in
    GCP" branch needed. `ca_bundle_path` is a local-machine-only concern
    (see FirestoreChaosRepository._build_client's own comment: grpc has its
    own TLS stack, unaffected by pip-system-certs) — never needed inside
    Cloud Run's own network."""
    from google.cloud import firestore

    if ca_bundle_path:
        os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", ca_bundle_path)
    if credentials_path:
        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            credentials_path
        )
        return firestore.Client(
            project=project_id, database=database_id, credentials=credentials
        )
    return firestore.Client(project=project_id, database=database_id)


def _commit_with_retry(batch: Any) -> None:
    """Commit a batch, retrying with exponential backoff on Firestore's
    transient ``ABORTED``/``Too much contention on these documents`` error —
    expected, documented Firestore behavior for a burst of writes into a
    recently-created collection (its automatic sharding hasn't warmed up
    yet), not a sign anything is actually wrong. Only ever hit while
    writing, never by the running app (which only ever reads). Does NOT
    catch ``InvalidArgument`` ("Transaction too big") — that one is handled
    one layer up, in ``_commit_pairs``, by splitting the chunk instead of
    retrying it unchanged (retrying an oversized commit would just fail the
    same way every time)."""
    from google.api_core.exceptions import Aborted, ServiceUnavailable

    delay = 1.0
    for attempt in range(1, _COMMIT_MAX_ATTEMPTS + 1):
        try:
            batch.commit()
            return
        except (Aborted, ServiceUnavailable) as exc:
            if attempt == _COMMIT_MAX_ATTEMPTS:
                raise
            print(
                f"  [retry] batch commit contention (attempt {attempt}): {exc}"
                f" — retrying in {delay:.1f}s"
            )
            time.sleep(delay)
            delay *= 2


def _commit_pairs(client: Any, pairs: list[tuple[Any, dict[str, Any]]]) -> None:
    """Commit a list of (doc_ref, data) pairs as one batch. If Firestore
    reports the transaction itself as too big, split the chunk in half and
    recurse — adapting to the REAL limit (which includes index-entry
    overhead this module can't predict exactly from Python, see
    _BATCH_BYTES_LIMIT's own comment) instead of guessing a fixed byte
    threshold that could still be wrong for some future, even larger
    species entry. A single document that is STILL too big on its own
    re-raises rather than looping forever — nothing left to split."""
    from google.api_core.exceptions import InvalidArgument

    if not pairs:
        return
    batch = client.batch()
    for ref, data in pairs:
        batch.set(ref, data)
    try:
        _commit_with_retry(batch)
    except InvalidArgument:
        if len(pairs) == 1:
            raise
        mid = len(pairs) // 2
        print(f"  [split] transaction too big for {len(pairs)} doc(s) — retrying as two halves")
        _commit_pairs(client, pairs[:mid])
        _commit_pairs(client, pairs[mid:])


def write_tier(
    client: Any,
    collection: str,
    tier_id: str,
    info: dict[str, Any],
    species_data: dict[str, dict[str, Any]],
    *,
    progress: bool = True,
) -> int:
    """Upsert one tier document plus every species document under it.
    Idempotent (``.set()`` overwrites) — safe to re-run on unchanged or
    updated data alike. Returns the number of species documents written."""
    tier_ref = client.collection(collection).document(tier_id)
    _commit_pairs(client, [(tier_ref, {"info": strip_empty_keys(info)})])

    from src.adapters.chaos.species_normalize import normalize_species

    pending: list[tuple[Any, dict[str, Any]]] = []
    pending_bytes = 0
    written = 0
    total = len(species_data)
    for species_name, mon_data in species_data.items():
        doc_ref = tier_ref.collection("species").document(normalize_species(species_name))
        clean_mon_data = strip_empty_keys(mon_data)
        doc_data = {**clean_mon_data, "original_name": species_name}
        doc_bytes = len(json.dumps(doc_data))
        # Flush BEFORE adding this doc whenever either limit would be
        # crossed by adding it — never after, so a chunk handed to
        # _commit_pairs starts as small as this first-pass heuristic can
        # make it (the real safety guarantee is _commit_pairs' own
        # adaptive splitting, not this estimate — see _BATCH_BYTES_LIMIT).
        if pending and (len(pending) >= _BATCH_COUNT_LIMIT or pending_bytes + doc_bytes > _BATCH_BYTES_LIMIT):
            _commit_pairs(client, pending)
            written += len(pending)
            if progress:
                print(f"  ... {written}/{total} species written")
            pending = []
            pending_bytes = 0
        pending.append((doc_ref, doc_data))
        pending_bytes += doc_bytes
    if pending:
        _commit_pairs(client, pending)
        written += len(pending)
    if progress:
        print(f"  done: {written}/{total} species written")
    return written
