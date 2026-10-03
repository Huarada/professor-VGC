"""Pasted Showdown replay URL -> replay JSON text.

Accepts ``play.pokemonshowdown.com/battle-<id>`` and
``replay.pokemonshowdown.com/<id>[.json]``, both normalized to
``https://replay.pokemonshowdown.com/<id>.json``. Other URLs are not treated
as replays.
"""

from __future__ import annotations

import re

import requests

from src.domain.exceptions import ReplayFetchError

_ID = r"[a-z0-9][a-z0-9-]*"
_BATTLE_URL_RE = re.compile(
    rf"^https?://play\.pokemonshowdown\.com/battle-(?P<id>{_ID})/?(?:\?.*)?$",
    re.IGNORECASE,
)
_REPLAY_URL_RE = re.compile(
    rf"^https?://replay\.pokemonshowdown\.com/(?P<id>{_ID})(?:\.json)?/?(?:\?.*)?$",
    re.IGNORECASE,
)


def normalize_replay_json_url(text: str) -> str | None:
    """The canonical JSON URL for a recognized replay URL, else None (safe to call
    on any pasted text).
    """
    candidate = text.strip()
    match = _BATTLE_URL_RE.match(candidate) or _REPLAY_URL_RE.match(candidate)
    if match is None:
        return None
    return f"https://replay.pokemonshowdown.com/{match.group('id')}.json"


def fetch_replay_json(url: str, timeout: float = 10.0) -> str:
    """GET the replay JSON; any failure raises ``ReplayFetchError``."""
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise ReplayFetchError(f"Could not reach {url}: {exc}") from exc
    if response.status_code != 200:
        raise ReplayFetchError(
            f"{url} returned HTTP {response.status_code} — the replay may "
            "have been deleted, made private, or the URL/ID is wrong."
        )
    text = response.text.strip()
    if not text:
        raise ReplayFetchError(f"{url} returned an empty response.")
    return text
