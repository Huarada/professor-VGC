"""Resolves a pasted Showdown replay URL into the raw replay JSON text, so
the "paste a replay" input accepts a URL as well as pasted JSON/log text.

Two URL shapes are recognized (both hosts Showdown itself links from —
verified live against a real replay before writing this):
  - the live/replay VIEWER link a user copies from their browser address
    bar, e.g. https://play.pokemonshowdown.com/battle-<id>
  - the JSON endpoint itself, with or without the .json suffix, e.g.
    https://replay.pokemonshowdown.com/<id>[.json]

Both normalize to the same canonical JSON endpoint
(https://replay.pokemonshowdown.com/<id>.json), which is fetched with one
GET request. Deliberately scoped to these two Showdown hosts only — not
"try to fetch any http(s) URL as replay data" — so a URL to something
else entirely fails fast and clearly rather than being silently treated
as a replay source.
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
    """Converts a recognized Showdown replay URL into its canonical JSON
    endpoint. Returns None for anything that isn't one of the two
    recognized shapes — pasted JSON or raw log text included, so this is
    safe to call unconditionally on whatever the user pasted; it will
    never misidentify replay content itself as a URL."""
    candidate = text.strip()
    match = _BATTLE_URL_RE.match(candidate) or _REPLAY_URL_RE.match(candidate)
    if match is None:
        return None
    return f"https://replay.pokemonshowdown.com/{match.group('id')}.json"


def fetch_replay_json(url: str, timeout: float = 10.0) -> str:
    """Fetches the raw replay JSON text from an already-normalized Showdown
    replay URL (see :func:`normalize_replay_json_url`). Raises
    ReplayFetchError — never a raw ``requests`` exception — on any network
    failure, non-200 response, or empty body, so the UI's existing
    ProfessorVGCError-based error handling covers this the same way it
    already covers every other failure mode, with no new except clause
    needed at the call site."""
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
