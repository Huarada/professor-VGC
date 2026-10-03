"""Species-name normalization: the in-memory key for local files and the
Firestore document id, so both backends resolve names identically.
"""

from __future__ import annotations


def normalize_species(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace(".", "").replace("'", "")
