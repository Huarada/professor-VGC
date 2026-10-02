"""Optional background music (see src/ui/assets/audio/README.md)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


# ---------------------------------------------------------------------------
# Background music — dedicated drop folder: src/ui/assets/audio/ (see the
# README.md in that folder). Drop a `theme.<ext>` file there and a small
# player appears in the sidebar automatically, no code edit needed. Same
# "starts empty, self-wiring" pattern as the backgrounds folder above: no
# file present is the default, working state (no player shown at all),
# not a gap — this project ships no music of its own.
# ---------------------------------------------------------------------------
_AUDIO_DIR = Path(__file__).resolve().parent / "assets" / "audio"


# Order matters when more than one theme.* file is present — first match wins.
_AUDIO_FORMATS: list[tuple[str, str]] = [
    (".mp3", "audio/mpeg"),
    (".mp4", "audio/mp4"),
    (".m4a", "audio/mp4"),
    (".wav", "audio/wav"),
    (".ogg", "audio/ogg"),
]


def _find_audio_file() -> tuple[Path, str] | None:
    """Looks for a user-dropped `theme.<ext>` file in src/ui/assets/audio/,
    tried in _AUDIO_FORMATS order. Returns the path plus its MIME type, or
    None if nobody has dropped one in — the common, fully-working default
    (see the module comment above)."""
    for ext, mime in _AUDIO_FORMATS:
        candidate = _AUDIO_DIR / f"theme{ext}"
        if candidate.is_file():
            return candidate, mime
    return None


def render_background_music() -> None:
    """Renders a small, native Streamlit audio player for the user-dropped
    track, if any, plus its own on/off checkbox — both are skipped
    entirely when no track is present, matching the rest of this file's
    "nothing to configure when the folder is empty" pattern. Requests
    autoplay (with sound, on page load) at the user's explicit request —
    NOTE this is a request, not a guarantee: browsers enforce their own
    autoplay policy for audio with sound (e.g. Chrome's Media Engagement
    Index) and will often silently block it on a visitor's very first
    visit regardless of what any site asks for, only allowing it once
    they've interacted with the page/domain before. There is no way for
    this app (or Streamlit itself) to override that from the server side —
    the checkbox and the player's own native pause control are the
    fallback for whenever the browser does block it. Loops once started,
    since this is meant as ambient background music, not a one-shot clip.
    `music_enabled` is a real Streamlit widget key, so unlike a plain
    variable it already persists across reruns on its own — unchecking it
    removes the player from the page entirely (not just pausing it), the
    most complete "off" available since nothing here holds a live handle
    to the browser's own audio element between reruns. To change either
    playback default, edit the st.audio(...) call below."""
    found = _find_audio_file()
    if found is None:
        return
    enabled = st.checkbox("Background music", value=True, key="music_enabled")
    if not enabled:
        return
    path, mime = found
    st.audio(str(path), format=mime, loop=True, autoplay=True)
