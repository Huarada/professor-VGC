"""Optional background music (see src/ui/assets/audio/README.md)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


# Optional background music: drop theme.<ext> into src/ui/assets/audio/ and a
# sidebar player appears. No file = no player.
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
    """The dropped ``theme.<ext>`` track and its MIME type, or None."""
    for ext, mime in _AUDIO_FORMATS:
        candidate = _AUDIO_DIR / f"theme{ext}"
        if candidate.is_file():
            return candidate, mime
    return None


def render_background_music() -> None:
    """A looping sidebar player with an on/off checkbox, when a track exists.
    Autoplay is requested, but browsers may block it until the visitor
    interacts; unchecking removes the player.
    """
    found = _find_audio_file()
    if found is None:
        return
    enabled = st.checkbox("Background music", value=True, key="music_enabled")
    if not enabled:
        return
    path, mime = found
    st.audio(str(path), format=mime, loop=True, autoplay=True)
