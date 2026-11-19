# Background music

Drop a single audio file here, named `theme.<ext>`. A "Background music"
checkbox appears in **Settings**. It starts off; enabling it reveals a player
that the visitor can start manually. Disabling it removes the player.

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

## Playback

Playback starts only when the visitor presses play. The track loops until
paused or the checkbox is turned off.

## How it's wired up

`src/ui/audio.py`'s `_find_audio_file()` looks for `theme.<ext>` here (see
`_AUDIO_FORMATS` for the exact list/order) and, if found,
`render_background_music()` renders the checkbox and, while it's checked,
the track itself via Streamlit's native `st.audio` widget — passed the
file path directly, so there's no manual encoding step the way the
background-image feature needs (`st.audio` serves a local file path on its
own). Both the checkbox and the player are skipped entirely when no track
is present. To change `loop`/`autoplay`, edit the `st.audio(...)` call in
`src/ui/audio.py`.

## Picking a track

- Keep it fairly small (a few MB, not tens) — Streamlit reads the whole
  file to serve it, and a large file makes the first load slower.
- Something ambient/loopable reads best on a loop; an obvious start/end
  will be noticeable every repeat.
