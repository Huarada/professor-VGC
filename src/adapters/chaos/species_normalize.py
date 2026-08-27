"""Species-name normalization shared by every Chaos storage backend.

Used both for the in-memory index the local-file backend builds over an
already-loaded file's keys (``ChaosRepository.mon_data``) and as the actual
Firestore DOCUMENT ID a species is stored/looked up under
(``FirestoreChaosRepository`` and the migration script) — the same function
either way, so a species resolves identically no matter which backend is
configured.
"""

from __future__ import annotations


def normalize_species(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace(".", "").replace("'", "")
