# Background music

Drop a single audio file here, named `theme.<ext>`, and a small player —
plus its own "Background music" on/off checkbox above it — appears in the
sidebar automatically, no code edit needed. Unchecking it removes the
player from the page entirely, not just pauses it.

| File | Format |
|---|---|
| `theme.mp3` | MP3 |
| `theme.mp4` / `theme.m4a` | MPEG-4 audio (AAC) |
| `theme.wav` | WAV |
| `theme.ogg` | Ogg Vorbis |

Only one of these is used — if more than one `theme.*` file is present,
the first match in the table order above wins.

## Nothing here yet? That's fine.

This is the default, working state. No player is shown at all when the
folder is empty — same "starts empty, self-wiring, no code edit needed"
pattern as [`src/ui/assets/backgrounds/`](../backgrounds/).

## Autoplay is requested, but not guaranteed

The player asks the browser to autoplay with sound on page load
(`st.audio(..., autoplay=True)`) and loops once started. This is a
*request*, not something this app (or Streamlit) can force: browsers
enforce their own autoplay policy for audio with sound (e.g. Chrome's
Media Engagement Index) and commonly block it on a visitor's very first
visit regardless of what the page asks for, only allowing it once
they've interacted with the page/domain before. When that happens the
player still shows up with visible controls (play/pause/volume/seek) —
the visitor starts it with one click — and the "Background music"
checkbox is there for turning it off either way.

## How it's wired up

`src/ui/app.py`'s `_find_audio_file()` looks for `theme.<ext>` here (see
`_AUDIO_FORMATS` for the exact list/order) and, if found,
`_render_background_music()` renders the checkbox and, while it's checked,
the track itself via Streamlit's native `st.audio` widget — passed the
file path directly, so there's no manual encoding step the way the
background-image feature needs (`st.audio` serves a local file path on its
own). Both the checkbox and the player are skipped entirely when no track
is present. To change `loop`/`autoplay`, edit the `st.audio(...)` call in
`src/ui/app.py` (search for `_render_background_music`).

## Picking a track

- Keep it fairly small (a few MB, not tens) — Streamlit reads the whole
  file to serve it, and a large file makes the first load slower.
- Something ambient/loopable reads best on a loop; an obvious start/end
  will be noticeable every repeat.
