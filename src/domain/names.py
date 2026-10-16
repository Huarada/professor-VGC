"""Name normalization shared by every layer.

Usage data (Smogon Chaos) names abilities, items and moves by id
(``"lifeorb"``, ``"friendguard"``) while battle logs use display names
(``"Life Orb"``); compare them by id, never by raw string.
"""

from __future__ import annotations

import re


def to_id(text: str | None) -> str:
    """Showdown-style id: lowercase alphanumerics only (``"Sunny Day"`` -> ``"sunnyday"``)."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())
