"""Streamlit presentation layer — pure view.

Collects input, calls a use case (a pipeline built by the Container), and
renders the returned DTO. Zero business logic, zero calc, zero prompt
engineering here.

Run with:  streamlit run src/ui/app.py
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import random
import re
import uuid
from pathlib import Path
from typing import cast
from urllib.parse import quote

import requests
import streamlit as st

from src.adapters.parsers.replay_viewer_parser import parse_replay_for_viewer
from src.adapters.replay_url_fetcher import fetch_replay_json, normalize_replay_json_url
from src.domain.exceptions import LLMProviderError, ProfessorVGCError, ReplayFetchError
from src.domain.models import AnalysisRequest
from src.domain.replay_view_models import BattleReplay, ReplayPokemonState, ReplayTurnSnapshot
from src.services.container import Container


def _get_container() -> Container:
    if "container" not in st.session_state:
        st.session_state["container"] = Container()
    # st.session_state's own __getitem__ is untyped (Any) by design (it's a
    # dynamic dict-like store) — cast() documents what we know we put in,
    # rather than letting Any silently propagate into every caller.
    return cast(Container, st.session_state["container"])


def _session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())
    return cast(str, st.session_state["session_id"])


# Substrings the underlying provider SDKs (openai, google-generativeai) use in
# their own error messages for a billing/quota shortfall vs. a transient rate
# limit — distinguished here only to point the user at the right fix, never
# to change control flow (the exception is already fatal for this request).
_QUOTA_HINTS = ("insufficient_quota", "credit_balance_exhausted", "no credits", "billing")
_RATE_LIMIT_HINTS = ("rate limit", "429", "resource_exhausted", "quota")


def _error_tip(exc: Exception) -> str:
    """A caption pointing at the likely fix, tailored to the failure category."""
    if isinstance(exc, LLMProviderError):
        text = str(exc).lower()
        if any(hint in text for hint in _QUOTA_HINTS):
            return (
                "Tip: your LLM provider account is out of credits/quota. Add "
                "billing at your provider's dashboard, or switch provider in "
                "the sidebar (openai ↔ gemini) if the other one still has "
                "credit — deterministic calcs are unaffected, only the final "
                "explanation needs the LLM."
            )
        if any(hint in text for hint in _RATE_LIMIT_HINTS):
            return (
                "Tip: the provider is rate-limiting requests. Wait a moment "
                "and try again, or switch provider in the sidebar."
            )
        return (
            "Tip: the LLM provider call failed. Check `PROFESSORVGC_OPENAI_API_KEY` / "
            "`PROFESSORVGC_GEMINI_API_KEY` are set and valid, and that the selected "
            "provider in the sidebar matches a key you actually have."
        )
    if isinstance(exc, ReplayFetchError):
        return (
            "Tip: the replay URL couldn't be fetched — double check it's a real, "
            "still-existing replay (a deleted or private one returns this same "
            "error), or paste the replay JSON/log text directly instead."
        )
    return (
        "Tip: paste the full Showdown replay JSON (including its \"log\" "
        "field), the raw battle log, or a Showdown replay URL. For provider "
        "errors, check your `PROFESSORVGC_OPENAI_API_KEY` / "
        "`PROFESSORVGC_GEMINI_API_KEY`."
    )


# ---------------------------------------------------------------------------
# Showdown-like battle replay panel.
#
# Deliberately independent of the LLM pipeline above: it consumes a
# BattleReplay from src.adapters.parsers.replay_viewer_parser (a standalone
# parser — see that module's docstring), never `result` (the AnalysisResult
# DTO). A bug here cannot affect the LLM answer, and vice versa.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Background images — dedicated drop folder: src/ui/assets/backgrounds/
# (see the README.md in that folder). Drop a `page.<ext>` file there for the
# whole-app wallpaper and/or a `battle-stage.<ext>` file for the battle
# panel's own backdrop — jpg/jpeg/png/webp, any resolution, picked up
# automatically on the next page load, no code edit needed. Nobody has
# dropped one in yet is the default, working state, not an error: the
# pure-CSS gradients below (_LAB_BACKGROUND_CSS_DEFAULT /
# _PAGE_BACKGROUND_CSS_DEFAULT) are complete backdrops on their own, and
# Showdown's own `fx/` background set has nothing lab-themed to fall back
# to (checked live — it's entirely outdoor/field-themed: grass, cave,
# beach...), so this project draws its own rather than leaving a gap.
# ---------------------------------------------------------------------------
_BACKGROUNDS_DIR = Path(__file__).resolve().parent / "assets" / "backgrounds"
_BACKGROUND_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# The battle stage's backdrop: a "genetics lab" wallpaper (think Mewtwo's
# containment chamber — sterile teal-blue walls, glowing overhead light,
# faint glass-tube paneling) instead of Showdown's plain outdoor field
# background. A gradient can never 404 — it's the same reliability
# principle as the sprite HEAD-check above, just applied to the one
# background every battle panel always shows.
_LAB_BACKGROUND_CSS_DEFAULT = (
    "radial-gradient(ellipse 60% 42% at 50% 0%, rgba(150,225,255,.22), transparent 70%),"
    "repeating-linear-gradient(90deg, rgba(120,210,255,.06) 0px, rgba(120,210,255,.06) 2px,"
    " transparent 2px, transparent 64px),"
    "linear-gradient(180deg, #0a1e24 0%, #123241 52%, #0a2129 100%)"
)

# PAGE-WIDE background — the whole app's backdrop (distinct from
# _LAB_BACKGROUND_CSS_DEFAULT above, which only skins the battle-panel
# stage and deliberately stays dark — see ADR-026). This one is a light
# "battle notebook" sky-blue, ported from a Figma Make design prototype: a
# soft sky gradient plus a faint graph-paper grid, standing in for that
# prototype's animated canvas (a static/CSS equivalent — see ADR-026 for why
# the canvas itself wasn't ported).
_PAGE_BACKGROUND_CSS_DEFAULT = (
    "radial-gradient(ellipse 70% 40% at 50% 0%, rgba(255,255,255,.55), transparent 70%),"
    "repeating-linear-gradient(0deg, rgba(37,99,168,.05) 0px, rgba(37,99,168,.05) 1px,"
    " transparent 1px, transparent 46px),"
    "repeating-linear-gradient(90deg, rgba(37,99,168,.05) 0px, rgba(37,99,168,.05) 1px,"
    " transparent 1px, transparent 46px),"
    "linear-gradient(160deg, #dbeeff 0%, #c8e4f8 35%, #b8d8f4 70%, #cce7fa 100%)"
)

# Design tokens (fonts + palette) ported from the Figma Make prototype's own
# App.tsx/index.css: Lora for headings (its italic weight doubles as the
# page's one "shimmer" accent), Nunito for body copy, Space Mono for
# labels/captions/data — plus the blue/ink/green/gold/purple accent palette
# that prototype's cards, badges and buttons actually used. Loaded once here
# and referenced as CSS custom properties everywhere below, instead of
# repeating literal hex values across every _*_html() builder in this file.
_DESIGN_TOKENS_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Nunito:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap');
:root {
    --pvgc-font-display: 'Lora', Georgia, serif;
    --pvgc-font-body: 'Nunito', system-ui, sans-serif;
    --pvgc-font-mono: 'Space Mono', monospace;
    --pvgc-ink: #1e2a4a;
    --pvgc-ink-muted: rgba(30, 42, 74, .55);
    --pvgc-blue: #2563a8;
    --pvgc-blue-dark: #1e3a8a;
    --pvgc-green: #1a7a50;
    --pvgc-orange: #c0400a;
    --pvgc-gold: #9b6a00;
    --pvgc-purple: #7030a0;
}
@keyframes pvgc-pulse {
    0% { box-shadow: 0 0 0 0 rgba(26, 122, 80, .45); }
    70% { box-shadow: 0 0 0 8px rgba(26, 122, 80, 0); }
    100% { box-shadow: 0 0 0 0 rgba(26, 122, 80, 0); }
}
"""

# Typography + native-widget theming: applies the tokens above to Streamlit's
# own DOM (headings, captions, widget labels, expanders, sidebar, buttons)
# via its documented-stable data-testid hooks, rather than hand-styling every
# individual st.* call in this file. Deliberately does NOT touch the
# battle-panel HTML (_battle_stage_html/_hp_box_html/etc.) — that stays on
# its own dark, Showdown-styled look, unaffected by the page theme around it.
_COMPONENT_THEME_CSS = """
html, body, [data-testid="stAppViewContainer"], .stApp {
    font-family: var(--pvgc-font-body);
}
h1, h2, h3, h4,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
    font-family: var(--pvgc-font-display) !important;
    color: var(--pvgc-ink);
}
[data-testid="stCaptionContainer"] {
    font-family: var(--pvgc-font-mono);
    letter-spacing: .02em;
}
[data-testid="stWidgetLabel"] p {
    font-family: var(--pvgc-font-mono) !important;
    font-size: .68rem !important;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--pvgc-ink-muted) !important;
}
[data-testid="stExpander"], [data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255, 255, 255, .62) !important;
    border: 1px solid rgba(37, 99, 168, .14) !important;
    border-radius: 16px !important;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
}
[data-testid="stExpander"] summary {
    font-family: var(--pvgc-font-display);
    color: var(--pvgc-ink);
}
[data-testid="stSidebarContent"] {
    background: rgba(255, 255, 255, .55);
    border-right: 1px solid rgba(37, 99, 168, .12);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
}
[data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input {
    border-radius: 12px !important;
    border: 1.5px solid rgba(37, 99, 168, .16) !important;
    background: rgba(37, 99, 168, .04) !important;
}
.stButton button[kind="primary"] {
    background: linear-gradient(135deg, var(--pvgc-blue-dark) 0%, var(--pvgc-blue) 100%) !important;
    border: none !important;
    border-radius: 12px !important;
    font-family: var(--pvgc-font-body);
    font-weight: 700 !important;
    box-shadow: 0 4px 20px rgba(37, 99, 168, .25);
}
.stButton button[kind="secondary"] {
    border-radius: 10px !important;
    border-color: rgba(37, 99, 168, .25) !important;
    color: var(--pvgc-blue) !important;
}
"""


def _find_background_image(stem: str) -> Path | None:
    """Looks for a user-dropped `<stem>.<ext>` file in
    src/ui/assets/backgrounds/, tried in _BACKGROUND_IMAGE_EXTENSIONS
    order. Returns None if nobody has dropped one in — the common case,
    not an error (see the module comment above)."""
    for ext in _BACKGROUND_IMAGE_EXTENSIONS:
        candidate = _BACKGROUNDS_DIR / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


@st.cache_data(show_spinner=False)
def _image_data_uri(path_str: str, mtime_ns: int) -> str:
    """Base64-encodes a local image file into a data: URI, cached by path +
    mtime (so replacing the file is picked up on the next load without a
    server restart). `st.cache_data`, not a bare module-level dict — a
    plain dict would be silently reset on every Streamlit rerun (every
    stepper click is a rerun) and this read+encode would run again every
    time for a potentially multi-MB wallpaper; see the ADR-015 follow-up
    on `_http_head_ok` above, which hit exactly this bug first."""
    path = Path(path_str)
    mime, _ = mimetypes.guess_type(path.name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


# Legibility scrims for a user-dropped background image, one per backdrop:
# the battle stage keeps its own dark scrim (its HP boxes/side-headers are
# styled for a dark backdrop regardless of the page theme around it — see
# ADR-026), while the page-wide backdrop now needs a light scrim to match
# this file's light "battle notebook" theme (dark ink-navy text over a
# darkened photo would otherwise be illegible).
_DARK_SCRIM_CSS = "linear-gradient(rgba(7,14,12,.72), rgba(7,14,12,.88))"
_LIGHT_SCRIM_CSS = "linear-gradient(rgba(219,238,255,.8), rgba(200,228,248,.9))"


def _background_css(stem: str, default_gradient: str, scrim: str = _DARK_SCRIM_CSS) -> str:
    """The layered `background` CSS value for either backdrop: a
    user-dropped image (see _find_background_image) behind a legibility
    scrim (dark for the battle stage, light for the page — see the constants
    above), or the built-in pure-CSS gradient if nobody has dropped one in
    yet."""
    image_path = _find_background_image(stem)
    if image_path is None:
        return default_gradient
    data_uri = _image_data_uri(str(image_path), image_path.stat().st_mtime_ns)
    return f"{scrim}, url('{data_uri}')"


def _inject_global_styles() -> None:
    """Paints the page-wide lab wallpaper onto Streamlit's own scrollable
    app container (its stable `data-testid` hooks, not `body` — Streamlit
    scrolls an inner container, not the page body itself), with
    `background-attachment: fixed` for a parallax feel: the wallpaper stays
    put in the viewport while the Q&A/analysis content scrolls over it, so
    it visibly "lags behind" — lowers into view — as the page scrolls down.
    Uses a user-dropped image from src/ui/assets/backgrounds/page.<ext> if
    present, else the built-in gradient — see _background_css."""
    page_background = _background_css("page", _PAGE_BACKGROUND_CSS_DEFAULT, _LIGHT_SCRIM_CSS)
    st.markdown(
        f"""
<style>
{_DESIGN_TOKENS_CSS}
{_COMPONENT_THEME_CSS}
[data-testid="stAppViewContainer"] {{
    background: {page_background};
    background-size: cover;
    background-attachment: fixed;
}}
[data-testid="stHeader"] {{
    background: transparent;
}}
</style>
""",
        unsafe_allow_html=True,
    )


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


def _render_background_music() -> None:
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


_DEFAULT_AVATAR = "https://play.pokemonshowdown.com/sprites/trainers/red.png"

# Real weather/field-condition icons from Showdown's own client (verified
# live against play.pokemonshowdown.com/fx/, filenames confirmed against the
# actual CSS at github.com/smogon/pokemon-showdown-client — battle.css's
# ".weather" background rules) — used instead of a generic emoji so the same
# situation gets the game's own asset. Trick Room is internally modeled as a
# "weather" by Showdown for this exact purpose (it shares this icon).
# Tailwind has no dedicated background icon in the real client either (it's
# shown there as plain text, not a graphic) — the badge for it stays icon-less
# below for the same reason, not as an oversight.
_WEATHER_ICON_FILES = {
    "sunnyday": "weather-sunnyday.jpg", "desolateland": "weather-sunnyday.jpg",
    "raindance": "weather-raindance.jpg", "primordialsea": "weather-raindance.jpg",
    "sandstorm": "weather-sandstorm.png",
    "hail": "weather-hail.png", "snow": "weather-hail.png", "snowscape": "weather-hail.png",
    "deltastream": "weather-strongwind.png",
    "mistyterrain": "weather-mistyterrain.png",
    "electricterrain": "weather-electricterrain.png",
    "grassyterrain": "weather-grassyterrain.png",
    "psychicterrain": "weather-psychicterrain.png",
    "gravity": "weather-gravity.png",
    "magicroom": "weather-magicroom.png",
    "trickroom": "weather-trickroom.png",
    "wonderroom": "weather-wonderroom.png",
}

# A tiny inline pokéball, the LAST fallback tier for any sprite/avatar image
# — so a missing sprite (confirmed live: several of this project's own
# Champions-format custom Megas have no sprite at any tested URL — see
# ADR-014) never renders as a broken-image icon.
_PLACEHOLDER_SPRITE = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<circle cx="32" cy="32" r="29" fill="#eee" stroke="#333" stroke-width="3"/>'
    '<path d="M3 32h58" stroke="#333" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="9" fill="#eee" stroke="#333" stroke-width="3"/>'
    "</svg>"
)

# The site-wide icon set: no emoji anywhere on the page. Two self-contained,
# hand-drawn SVG glyphs (inline data URIs, zero network dependency — the
# same reliability principle behind the sprite HEAD-check above) stand in
# for every emoji the page used to carry. A colored pokéball marks branding/
# neutral chrome (page icon, title, plain info notes); a minimalist Pikachu
# face marks a "noteworthy" moment — a caveat/warning, or the play that was
# actually taken. Explicit width/height on the <svg> root means both render
# small out of the box even through plain Markdown image syntax (no HTML
# needed), since `st.info`/`st.warning` bodies are Markdown-only.
_POKEBALL_ICON = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="18" height="18">'
    '<circle cx="32" cy="32" r="29" fill="#fff" stroke="#222" stroke-width="3"/>'
    '<path d="M3 32a29 29 0 0 1 58 0z" fill="#ee1515" stroke="#222" stroke-width="3"/>'
    '<path d="M3 32h22M39 32h22" stroke="#222" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="9" fill="#fff" stroke="#222" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="3.5" fill="#222"/>'
    "</svg>"
)
_PIKACHU_ICON = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="18" height="18">'
    '<path d="M14 4 L22 26 L10 24 Z" fill="#222"/>'
    '<path d="M50 4 L54 24 L42 26 Z" fill="#222"/>'
    '<path d="M16 8 L22 25 L12 23 Z" fill="#f6d02f"/>'
    '<path d="M48 8 L52 23 L42 25 Z" fill="#f6d02f"/>'
    '<circle cx="32" cy="34" r="20" fill="#f6d02f" stroke="#222" stroke-width="2.5"/>'
    '<circle cx="24" cy="32" r="2.6" fill="#222"/>'
    '<circle cx="40" cy="32" r="2.6" fill="#222"/>'
    '<ellipse cx="16" cy="40" rx="5" ry="3.5" fill="#e2483d"/>'
    '<ellipse cx="48" cy="40" rx="5" ry="3.5" fill="#e2483d"/>'
    "</svg>"
)


def _icon_md(uri: str) -> str:
    """Markdown-image form of an icon, for use inside st.info/st.warning
    bodies — those render plain Markdown, not raw HTML, so an <img> tag
    would be escaped rather than displayed."""
    return f"![]({uri})"


def _icon_html(uri: str, size: int = 18) -> str:
    """A sized icon, for spots already using unsafe_allow_html=True (title
    bar, battle-panel banners, inline captions built as HTML strings).

    Reported: the hero pokeball stayed pinned to a small icon size no
    matter what `size` was passed. Root cause, found by inspecting
    Streamlit's own frontend bundle (StreamlitMarkdown.*.js): every `img`
    rendered inside a markdown container gets a built-in `max-height: 1em`
    rule (meant for genuinely inline icons/emoji within a line of text).
    A first attempt fought this with an explicit `!important` `max-height`
    on the `<img>` itself — that should win on paper (inline style is
    higher-priority author origin than an external/emotion-injected rule
    at equal `!important` weight) but still rendered small in the real
    app, confirmed after a full process restart and hard browser refresh
    ruled out a stale-cache explanation. Rather than keep fighting a CSS
    war against a rule this file doesn't control the exact specificity
    of, this renders as a `<span>` with a CSS `background-image` instead
    of an `<img>` element — Streamlit's rule is scoped to the `img` tag
    selector by construction, so a `<span>` simply never matches it,
    regardless of how that cascade war would have resolved. `_icon_md`'s
    small inline warning/info icons go through Streamlit's native
    Markdown image syntax (a real `<img>`) instead of this function and
    are unaffected — that ~1em cap is correct, wanted behavior there."""
    return (
        f'<span role="img" aria-label="" style="display:inline-block;'
        f"width:{size}px;height:{size}px;flex-shrink:0;vertical-align:middle;"
        f"background-image:url('{uri}');background-size:contain;"
        f'background-repeat:no-repeat;background-position:center;"></span>'
    )


# The loading overlay's rotating captions, ported from the Figma design's
# LoadingScreen component (originally driven by a JS setInterval). Reproduced
# here as pure CSS keyframes instead — see _loading_overlay_html — since
# script tags injected via st.markdown(unsafe_allow_html=True) are not
# reliably executed by Streamlit's frontend (it renders that HTML via
# innerHTML, which browsers don't execute dynamically-inserted <script> tags
# from), so a setInterval-based version would silently never run.
_LOADING_STEPS = [
    "Reading battle replay log…",
    "Parsing Pokémon team sets…",
    "Computing turn-by-turn damage…",
    "Querying current metagame data…",
    "Generating coach analysis…",
]
_LOADING_STEP_SECONDS = 1.1


def _loading_overlay_html() -> str:
    """A big, centered, spinning pokeball covering the whole viewport, with
    a pulsing ring, rotating status captions and progress dots underneath —
    the explicit "still processing" indicator for the full duration of an
    analysis, so it's never ambiguous whether the app is done or still
    working. Reuses _PLACEHOLDER_SPRITE's black-and-white pokeball (the
    same shape already used elsewhere in this file for a missing sprite)
    rather than the branded red/white _POKEBALL_ICON — deliberately
    monochrome, as asked for, not colored. The captions/dots below cycle via
    the CSS-only staggered-animation trick documented on _LOADING_STEPS:
    every span shares one keyframe/duration and differs only by
    animation-delay, so they take turns being visible without any JS."""
    n = len(_LOADING_STEPS)
    total = n * _LOADING_STEP_SECONDS
    window_pct = 100 / n
    caption_spans = "".join(
        f'<span style="position:absolute;inset:0;display:flex;align-items:center;'
        f'justify-content:center;opacity:0;text-align:center;padding:0 12px;'
        f'animation:pvgc-step-fade {total}s ease-in-out {i * _LOADING_STEP_SECONDS}s infinite;">'
        f"{html.escape(step)}</span>"
        for i, step in enumerate(_LOADING_STEPS)
    )
    dot_spans = "".join(
        f'<span style="display:inline-block;height:6px;border-radius:999px;'
        f"background:rgba(37,99,168,.22);margin:0 3px;"
        f'animation:pvgc-dot-active {total}s steps(1) {i * _LOADING_STEP_SECONDS}s infinite;"></span>'
        for i in range(n)
    )
    return f"""
<style>
@keyframes pvgc-spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
@keyframes pvgc-pulse-ring {{
    0% {{ box-shadow: 0 0 0 0 rgba(37,99,168,.28); }}
    70% {{ box-shadow: 0 0 0 26px rgba(37,99,168,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(37,99,168,0); }}
}}
@keyframes pvgc-step-fade {{
    0% {{ opacity: 0; }}
    2% {{ opacity: 1; }}
    {window_pct - 2:.2f}% {{ opacity: 1; }}
    {window_pct:.2f}% {{ opacity: 0; }}
    100% {{ opacity: 0; }}
}}
@keyframes pvgc-dot-active {{
    0% {{ width: 6px; background: rgba(37,99,168,.22); }}
    2% {{ width: 22px; background: #2563a8; }}
    {window_pct - 2:.2f}% {{ width: 22px; background: #2563a8; }}
    {window_pct:.2f}% {{ width: 6px; background: rgba(37,99,168,.22); }}
    100% {{ width: 6px; background: rgba(37,99,168,.22); }}
}}
</style>
<div style="position:fixed;inset:0;background:rgba(219,238,255,.88);
            display:flex;align-items:center;justify-content:center;z-index:999999;
            font-family:'Nunito',system-ui,sans-serif;">
  <div style="display:flex;flex-direction:column;align-items:center;gap:26px;">
    <div style="position:relative;width:120px;height:120px;display:flex;
                align-items:center;justify-content:center;border-radius:50%;
                animation:pvgc-pulse-ring 2s ease-out infinite;">
      <img src="{_PLACEHOLDER_SPRITE}" alt="Loading"
           style="width:90px;height:90px;animation:pvgc-spin 1s linear infinite;
                  filter:drop-shadow(0 4px 18px rgba(0,0,0,.3));" />
    </div>
    <div style="text-align:center;">
      <p style="font-family:'Lora',Georgia,serif;font-style:italic;font-weight:600;
                font-size:1.25rem;color:#1e3a8a;margin:0 0 10px;">
        Analysing your battle
      </p>
      <div style="position:relative;height:1.1rem;min-width:280px;
                  font-family:'Space Mono',monospace;font-size:.7rem;letter-spacing:.03em;
                  color:rgba(30,58,138,.55);">
        {caption_spans}
      </div>
    </div>
    <div>{dot_spans}</div>
  </div>
</div>
"""


# Front (opponent, top of the stage) / back (own side, bottom) sprite folders,
# tried in this order. Verified live: Showdown's sprite IDs are idiosyncratic
# per-species — a Mega/regional-forme suffix keeps ONE hyphen before the
# (concatenated) suffix ("Charizard-Mega-Y" -> "charizard-megay"), but a
# species whose real name itself contains a hyphen drops it entirely
# ("Porygon-Z" -> "porygonz", "Kommo-o" -> "kommoo") — there is no way to
# tell these apart from the string alone without the game's own species
# table, so both candidate IDs are tried at every tier.
_FRONT_SPRITE_TIERS = [("ani", "gif"), ("gen5", "png")]
_BACK_SPRITE_TIERS = [("ani-back", "gif"), ("gen5-back", "png")]
_STAT_LABELS = {"atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}


def _sprite_id_candidates(species: str) -> list[str]:
    parts = [p.lower() for p in species.split("-") if p]
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]
    hyphenated = parts[0] + "-" + "".join(parts[1:])
    squashed = "".join(parts)
    return [hyphenated, squashed] if hyphenated != squashed else [hyphenated]


def _battle_sprite_urls(species: str, forme: str, *, back: bool) -> list[str]:
    tiers = _BACK_SPRITE_TIERS if back else _FRONT_SPRITE_TIERS
    ids = (_sprite_id_candidates(forme) if forme else []) + _sprite_id_candidates(species)
    return [
        f"https://play.pokemonshowdown.com/sprites/{folder}/{sid}.{ext}"
        for folder, ext in tiers
        for sid in ids
    ]


def _dex_icon_urls(species: str) -> list[str]:
    return [
        f"https://play.pokemonshowdown.com/sprites/dex/{sid}.png"
        for sid in _sprite_id_candidates(species)
    ]


def _avatar_urls(avatar: str) -> list[str]:
    urls = []
    avatar = avatar.strip().lower()
    if avatar and not avatar.isdigit():  # numeric Showdown avatar IDs aren't resolvable to a filename
        urls.append(f"https://play.pokemonshowdown.com/sprites/trainers/{avatar}.png")
    urls.append(_DEFAULT_AVATAR)
    return urls


@st.cache_data(show_spinner=False, ttl=3600)
def _http_head_ok(url: str, timeout: float = 2.0) -> bool:
    """The actual HEAD check, cached via st.cache_data — NOT a bare
    module-level dict (that was the bug, see below). Raises on any
    network-level failure instead of swallowing it to a bool: st.cache_data
    only memoizes a *return*, never an exception, so a timeout/DNS/TLS
    failure is simply retried next call instead of being permanently
    memoized as a false negative — the same "only cache a definitive HTTP
    response" guarantee as before, now for free from the cache primitive
    itself rather than hand-rolled."""
    resp = requests.head(url, timeout=timeout, allow_redirects=True)
    return resp.status_code == 200


def _url_is_reachable(url: str, timeout: float = 2.0) -> bool:
    """Server-side, cached check of whether a sprite/avatar URL actually
    resolves. Reported: Kommo-o and a custom Mega Delphox intermittently
    showed as a broken image even though the correct URL was present later
    in the client-side onerror fallback list — relying purely on the
    browser retrying a sequence of failed image loads proved unreliable in
    some deployments. This resolves it once, server-side, so the emitted
    <img src> is already the verified-correct one whenever possible — the
    onerror cascade in _cascade_img_html stays only as a defensive
    fallback, not the primary resolution mechanism.

    Reported regression: turn-stepping became noticeably slow after this
    was first added. Root cause was the cache itself — it was a plain
    module-level dict, but Streamlit re-executes the ENTIRE script top to
    bottom on every rerun (every stepper click is a rerun), which
    re-executes `_URL_REACHABLE_CACHE: dict = {}` too, silently wiping it
    every single time. The cache was never actually surviving between
    clicks — every turn-step was re-running live network HEAD requests for
    every sprite on screen. Fixed by moving the cached check into
    `_http_head_ok` above, decorated with `st.cache_data`, Streamlit's own
    primitive for state that must survive reruns (and, as a bonus, survives
    across sessions on the same server process too, so this cost is now
    paid once per URL ever, not once per URL per rerun)."""
    try:
        return _http_head_ok(url, timeout)
    except requests.RequestException:
        return False


def _resolve_primary(urls: list[str]) -> list[str]:
    """Move the first server-verified-reachable URL in `urls` to the front,
    stopping at the first success (never verifies the whole list — most
    Pokemon resolve on the very first candidate, one HEAD request)."""
    for i, u in enumerate(urls):
        if u.startswith("data:") or _url_is_reachable(u):
            return [u] + urls[:i] + urls[i + 1 :]
    return urls


def _cascade_img_html(urls: list[str], alt: str, style: str) -> str:
    """An <img> whose primary src is server-verified (see _resolve_primary),
    with a client-side onerror cascade through the remaining candidates as a
    defensive fallback, ending at the pokéball placeholder — so a missing
    sprite/avatar at any tier never shows a broken-image icon. Fallback URLs
    travel as a JSON array in a data attribute; every URL here is either our
    own percent-encoded data: URI or a plain https:// sprite path, neither of
    which can contain a raw quote character, so no further escaping of the
    JSON itself is needed beyond the single-quoted HTML attribute wrapper.
    """
    seen: list[str] = []
    for u in _resolve_primary(urls):
        if u and u not in seen:
            seen.append(u)
    if not seen:
        seen = [_PLACEHOLDER_SPRITE]
    if seen[-1] != _PLACEHOLDER_SPRITE:
        seen.append(_PLACEHOLDER_SPRITE)
    primary, rest = seen[0], seen[1:]
    onerror = (
        "var u=JSON.parse(this.dataset.u);var i=(this.dataset.i|0);"
        "if(i<u.length){this.src=u[i];this.dataset.i=i+1;}else{this.onerror=null;}"
    )
    return (
        f'<img src="{primary}" data-u=\'{json.dumps(rest)}\' onerror="{onerror}" '
        f'alt="{html.escape(alt)}" style="{style}" />'
    )


def _condition_icon_url(condition: str) -> str | None:
    """The real Showdown fx icon for this field-condition label, if one
    exists (see _WEATHER_ICON_FILES above for which do and don't)."""
    if condition == "Trick Room":
        name = "trickroom"
    elif condition.startswith("weather "):
        name = condition[len("weather "):].lower().replace(" ", "")
    else:
        return None
    fname = _WEATHER_ICON_FILES.get(name)
    return f"https://play.pokemonshowdown.com/fx/{fname}" if fname else None


def _condition_badge_html(condition: str) -> str:
    icon_url = _condition_icon_url(condition)
    icon_html = (
        f'<img src="{icon_url}" alt="" style="width:15px;height:15px;border-radius:3px;'
        f"vertical-align:middle;margin-right:4px;object-fit:cover;\" "
        f"onerror=\"this.style.display='none';\" />"
        if icon_url
        else ""
    )
    return (
        f'<span style="display:inline-flex;align-items:center;background:#00000012;'
        f"border:1px solid #00000022;border-radius:4px;padding:1px 7px;margin:0 4px 4px 0;"
        f'font-size:11px;color:inherit;">{icon_html}'
        f"{html.escape(condition)}</span>"
    )


def _hp_color(pct: float) -> str:
    if pct > 50:
        return "#4caf50"
    if pct > 20:
        return "#ffb300"
    return "#e53935"


def _boost_multiplier(stage: int) -> float:
    stage = max(-6, min(6, stage))
    return (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)


def _format_multiplier(mult: float) -> str:
    return f"{mult:.2f}".rstrip("0").rstrip(".")


def _boost_badges_html(boosts: dict[str, int]) -> str:
    badges = []
    for stat, stage in boosts.items():
        if stage == 0:
            continue
        color = "#2e7d32" if stage > 0 else "#c62828"  # up = green, drop = red (matches the game)
        label = _STAT_LABELS.get(stat, stat.upper())
        badges.append(
            f'<span style="font-size:9px;background:{color}1a;color:{color};'
            f'border:1px solid {color};border-radius:3px;padding:0 3px;margin-right:2px;">'
            f"{_format_multiplier(_boost_multiplier(stage))}× {label}</span>"
        )
    return "".join(badges)


def _hp_box_html(state: ReplayPokemonState) -> str:
    """Showdown's own HP-bar UI: name + level, a colored bar, HP%, status
    and stat-stage badges — the compact info box shown above each sprite."""
    pct = 0.0 if state.fainted else max(0.0, min(100.0, state.hp_percent))
    bar_color = "#888" if state.fainted else _hp_color(pct)
    name = html.escape(state.species)
    forme_note = (
        f' <span style="font-weight:400;font-size:9px;color:#777;">'
        f"({html.escape(state.forme)})</span>"
        if state.forme
        else ""
    )
    status_badge = (
        f'<span style="font-size:9px;background:#c62828;color:#fff;border-radius:2px;'
        f'padding:0 3px;margin-right:3px;">{html.escape(state.status.upper())}</span>'
        if state.status
        else ""
    )
    fainted_note = (
        '<span style="font-size:9px;color:#e53935;">fainted</span>' if state.fainted else ""
    )
    boost_html = _boost_badges_html(state.boosts)
    return f"""
<div style="background:#fafafaf0;border:1px solid #333;border-radius:5px;padding:3px 6px;
            min-width:98px;max-width:132px;box-shadow:1px 2px 4px rgba(0,0,0,.35);">
  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:6px;">
    <span style="font-size:12px;font-weight:700;color:#222;white-space:nowrap;
                 overflow:hidden;text-overflow:ellipsis;">{name}{forme_note}</span>
    <span style="font-size:10px;color:#555;flex-shrink:0;">L50</span>
  </div>
  <div style="background:#00000022;border-radius:3px;overflow:hidden;height:6px;margin:2px 0 1px;">
    <div style="width:{pct}%;height:100%;background:{bar_color};"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:9px;color:#444;">
    <span>{status_badge}{fainted_note}</span><span>{pct:.0f}%</span>
  </div>
  {f'<div style="margin-top:2px;">{boost_html}</div>' if boost_html else ""}
</div>
"""


def _team_icon_html(species: str, *, alive: bool, active: bool) -> str:
    img = _cascade_img_html(
        _dex_icon_urls(species), species,
        f"width:22px;height:22px;border-radius:3px;image-rendering:pixelated;"
        f"{'filter:grayscale(1);' if not alive else ''}",
    )
    border = "2px solid #ffd600" if active else "1px solid #ffffff55"
    opacity = "1" if alive else "0.4"
    return (
        f'<span style="display:inline-block;border:{border};border-radius:4px;'
        f'margin:0 1px;opacity:{opacity};">{img}</span>'
    )


def _side_header_html(replay: BattleReplay, snapshot: ReplayTurnSnapshot, player: str, align: str) -> str:
    """Name + avatar + team-icon tray for one side, as a normal-flow block
    (NOT absolutely positioned — see _battle_stage_html for why: a corner
    overlay collides with the sprite rows the moment the panel is narrower
    than Showdown's own wide desktop layout, which this column always is)."""
    name = html.escape(replay.player_names.get(player, player))
    avatar_img = _cascade_img_html(
        _avatar_urls(replay.avatars.get(player, "")), name,
        "width:26px;height:26px;border-radius:50%;border:2px solid #fff;"
        "box-shadow:1px 1px 3px rgba(0,0,0,.5);vertical-align:middle;",
    )
    roster = replay.team.get(player) or list(snapshot.pokemon.get(player, {}))
    active_here = set(snapshot.active.get(player, []))
    side_pokemon = snapshot.pokemon.get(player, {})
    icons = "".join(
        _team_icon_html(
            sp,
            alive=not side_pokemon.get(sp, ReplayPokemonState(species=sp)).fainted,
            active=sp in active_here,
        )
        for sp in roster
    )
    name_order = (
        f'<span style="margin-right:6px;">{name}</span>{avatar_img}'
        if align == "right"
        else f'{avatar_img}<span style="margin-left:6px;">{name}</span>'
    )
    return f"""
<div style="text-align:{align};">
  <div style="font-size:11px;font-weight:700;color:#fff;text-shadow:1px 1px 2px #000;">
    {name_order}
  </div>
  <div style="margin-top:2px;">{icons}</div>
</div>
"""


def _mon_slot_html(snapshot: ReplayTurnSnapshot, player: str, species: str, *, back: bool) -> str:
    state = snapshot.pokemon.get(player, {}).get(species) or ReplayPokemonState(species=species)
    size = "84px" if back else "68px"
    fainted_style = "opacity:.4;filter:grayscale(.6);" if state.fainted else ""
    sprite_img = _cascade_img_html(
        _battle_sprite_urls(state.species, state.forme, back=back), state.species,
        f"width:{size};height:{size};image-rendering:pixelated;{fainted_style}",
    )
    return f"""
<div style="display:flex;flex-direction:column;align-items:center;gap:3px;">
  {_hp_box_html(state)}
  {sprite_img}
</div>
"""


def _battle_stage_html(replay: BattleReplay, snapshot: ReplayTurnSnapshot) -> str:
    """The field scene: background, both sides' avatars/team icons, and
    their active Pokemon (opponent front sprites up top, own side back
    sprites at the bottom). Everything is normal document flow (a column of
    stacked rows with `gap`), NOT absolutely positioned over a fixed
    aspect-ratio box — this panel lives in a narrow column (roughly a third
    of the page), not Showdown's own wide desktop layout, so a fixed 16:9
    shape with overlaid corners left no room for two rows of sprites and
    overlapped everything. Flow layout instead grows to whatever height the
    content actually needs, at any column width."""
    players = sorted(snapshot.active) or sorted(replay.player_names) or ["p1", "p2"]
    p1 = players[0]
    p2 = players[1] if len(players) > 1 else players[0]
    p1_slots = "".join(
        _mon_slot_html(snapshot, p1, sp, back=True) for sp in snapshot.active.get(p1, [])
    )
    p2_slots = "".join(
        _mon_slot_html(snapshot, p2, sp, back=False) for sp in snapshot.active.get(p2, [])
    )
    turn_label = "Leads" if snapshot.turn == 0 else f"Turn {snapshot.turn}"
    stage_background = _background_css("battle-stage", _LAB_BACKGROUND_CSS_DEFAULT)

    return f"""
<div style="width:100%;border-radius:10px;overflow:hidden;
            background:{stage_background};border:2px solid #333;
            box-shadow:inset 0 -40px 60px -25px rgba(0,0,0,.55),
                       inset 0 40px 50px -30px rgba(0,0,0,.4),
                       0 2px 8px rgba(0,0,0,.4);
            display:flex;flex-direction:column;
            gap:10px;padding:8px 5% 12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div style="background:#fffdf0;border:2px solid #333;border-radius:6px;
                padding:2px 10px;font-weight:800;font-size:12px;">
      {turn_label}
    </div>
    <div style="max-width:64%;">{_side_header_html(replay, snapshot, p2, "right")}</div>
  </div>
  <div style="display:flex;justify-content:space-around;align-items:flex-end;gap:8px;flex-wrap:wrap;">
    {p2_slots}
  </div>
  <div style="display:flex;justify-content:space-around;align-items:flex-end;gap:8px;flex-wrap:wrap;">
    {p1_slots}
  </div>
  <div style="max-width:64%;">{_side_header_html(replay, snapshot, p1, "left")}</div>
</div>
"""


def _step_turn(delta: int, max_idx: int) -> None:
    current = st.session_state.get("turn_index", 0)
    st.session_state["turn_index"] = max(0, min(max_idx, current + delta))


def _current_turn_number(replay: BattleReplay) -> int:
    """Maps the stepper's turn_index (an array index into replay.snapshots)
    to the actual in-game turn number that snapshot represents (0 for
    "Leads") — the number the LLM's answer and the turn-by-turn/protect-read
    checks are both keyed by, used to highlight whichever of their entries
    matches the turn currently selected on the slider."""
    if not replay.snapshots:
        return 0
    idx = cast(int, st.session_state.get("turn_index", 0))
    idx = max(0, min(idx, len(replay.snapshots) - 1))
    return replay.snapshots[idx].turn


# Matches a turn breakdown ONLY at the start of a line — "**Turn 3**:",
# "3. **Turn 3**:", plain "Turn 3:" — never a mid-sentence, incidental
# aside like "...capitalized on the play from Turn 3..." inside a closing
# summary paragraph, which would wrongly get treated as a new segment.
_TURN_HEADER_RE = re.compile(
    r"^(?:\d+\.\s*)?(?:\*\*)?Turn\s+(?P<turn>\d+)\b[:.]?(?:\*\*)?[:.]?",
    re.IGNORECASE | re.MULTILINE,
)


def _highlight_answer_by_turn(answer_md: str, turn: int) -> str:
    """Wraps the paragraph/list item narrating the given turn in
    Streamlit's own `:orange-background[...]` markdown directive, so
    moving the stepper visually ties the LLM's narrative to the turn it's
    about (deliberately not a raw HTML <mark> tag: CommonMark treats a tag
    placed at a line's start as an HTML block, which would stop the
    enclosed **bold**/list markdown from being parsed at all — Streamlit's
    directive has no such edge case and needs no unsafe_allow_html). If the
    answer never breaks itself down by turn — a normal, common case for
    many questions — no match is found and the text renders unchanged."""
    matches = list(_TURN_HEADER_RE.finditer(answer_md))
    if not matches:
        return answer_md
    pieces: list[str] = []
    last_end = 0
    for i, m in enumerate(matches):
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(answer_md)
        pieces.append(answer_md[last_end:m.start()])
        segment = answer_md[m.start():seg_end]
        if int(m.group("turn")) == turn:
            stripped = segment.rstrip("\n")
            trailer = segment[len(stripped):]
            # The directive can't cleanly span a paragraph break, so if this
            # turn's write-up has more than one paragraph, highlight just
            # the first — a harmless degradation, not a rendering break.
            para_end = stripped.find("\n\n")
            head, tail = (stripped, "") if para_end == -1 else (stripped[:para_end], stripped[para_end:])
            segment = f":orange-background[{head}]" + tail + trailer
        pieces.append(segment)
        last_end = seg_end
    pieces.append(answer_md[last_end:])
    return "".join(pieces)


def _render_battle_panel(replay: BattleReplay) -> None:
    max_idx = len(replay.snapshots) - 1
    if "turn_index" not in st.session_state:
        st.session_state["turn_index"] = 0  # default: first turn (Leads)
    # Clamp defensively — a new replay may have fewer turns than whatever
    # was selected while viewing a previous one.
    st.session_state["turn_index"] = max(0, min(max_idx, st.session_state["turn_index"]))

    col_prev, col_slider, col_next = st.columns([1, 6, 1])
    with col_prev:
        st.button(
            "◀", key="prev_turn", use_container_width=True,
            on_click=_step_turn, args=(-1, max_idx),
            disabled=st.session_state["turn_index"] <= 0,
        )
    with col_next:
        st.button(
            "▶", key="next_turn", use_container_width=True,
            on_click=_step_turn, args=(1, max_idx),
            disabled=st.session_state["turn_index"] >= max_idx,
        )
    with col_slider:
        st.slider("Turn", 0, max_idx, key="turn_index", label_visibility="collapsed")

    snapshot: ReplayTurnSnapshot = replay.snapshots[st.session_state["turn_index"]]
    st.markdown(_battle_stage_html(replay, snapshot), unsafe_allow_html=True)

    if snapshot.conditions:
        st.markdown(
            "".join(_condition_badge_html(c) for c in snapshot.conditions),
            unsafe_allow_html=True,
        )

    message = "<br/>".join(html.escape(line) for line in snapshot.log) or "&nbsp;"
    st.markdown(
        f'<div style="background:#2b2b2bf2;color:#f5f5f5;border-radius:6px;'
        f'padding:8px 12px;font-size:13px;min-height:20px;line-height:1.5;'
        f'margin-top:4px;">{message}</div>',
        unsafe_allow_html=True,
    )

    if st.session_state["turn_index"] == max_idx:
        # No emoji here either: the real client has no "forfeit flag"/"trophy"
        # graphic (a forfeit is just a plain log line there) — a winner IS
        # shown with their own avatar, so that's what stands in for a trophy.
        if replay.forfeited_player:
            name = html.escape(
                replay.player_names.get(replay.forfeited_player, replay.forfeited_player)
            )
            st.markdown(
                f'<div style="background:#c6282822;border:1px solid #c6282855;color:#c62828;'
                f'border-radius:6px;padding:6px 10px;font-size:13px;margin-top:6px;">'
                f"{name} forfeited this game.</div>",
                unsafe_allow_html=True,
            )
        if replay.winner_player:
            name = html.escape(
                replay.player_names.get(replay.winner_player, replay.winner_player)
            )
            avatar_img = _cascade_img_html(
                _avatar_urls(replay.avatars.get(replay.winner_player, "")), name,
                "width:22px;height:22px;border-radius:50%;vertical-align:middle;margin-right:6px;",
            )
            st.markdown(
                f'<div style="background:#2e7d3222;border:1px solid #2e7d3255;color:#2e7d32;'
                f'border-radius:6px;padding:6px 10px;font-size:13px;margin-top:6px;'
                f'display:flex;align-items:center;">{avatar_img}Winner: {name}</div>',
                unsafe_allow_html=True,
            )


def _hero_header_html() -> str:
    """The page's top identity bar — logo, name, and a pulsing "AI ready"
    status dot, ported from the Figma design's sticky nav (kept as a normal
    flow block here rather than `position: sticky`, since a truly sticky
    child of Streamlit's own scroll container is unreliable across
    versions)."""
    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
            gap:10px;padding:2px 2px 16px;border-bottom:1px solid rgba(37,99,168,.14);
            margin-bottom:20px;">
  <div style="display:flex;align-items:center;gap:12px;">
    {_icon_html(_POKEBALL_ICON, size=34)}
    <span style="font-family:var(--pvgc-font-display);font-weight:700;font-size:1.9rem;
                 line-height:1.15;color:var(--pvgc-ink);">ProfessorVGC</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px;">
    <span style="width:7px;height:7px;border-radius:50%;background:var(--pvgc-green);
                 animation:pvgc-pulse 2s infinite;"></span>
    <span style="font-family:var(--pvgc-font-mono);font-size:.62rem;letter-spacing:.1em;
                 color:var(--pvgc-ink-muted);">AI READY</span>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Idle-state marketing content — hero copy, tag pills, feature cards, and the
# decorative background texture — ported from the Figma design's landing
# copy (the `phase === 'idle'` section of its App.tsx). Rendered only while
# there's no analysis yet (see main()): once a replay has been analyzed,
# this decorative block would just push the real Answer/battle panel further
# down the page for no benefit, so it's hidden exactly like the prototype's
# own idle-only feature-card grid.
# ---------------------------------------------------------------------------

# A handful of real formulas/constants from this project's own deterministic
# core (STAB, EVs capped at 508, Tailwind doubling Speed for 4 turns, the
# 85–100% damage roll) — same decorative role as the Figma prototype's
# "Pokemon equations" but accurate to what ProfessorVGC actually computes,
# not invented flavor text. Scattered at fixed, deterministic positions (not
# `random.random()`, which would jitter on every Streamlit rerun) at very
# low opacity, behind all real content.
_AMBIENT_EQUATIONS = [
    "DMG = ((2*Level/5+2)*Power*ATK/DEF)/50+2",
    "STAB * TYPE_EFF * RAND[0.85, 1.00]",
    "Tailwind: SPD x2, turns <= 4",
    "EV_total <= 508, EV_per_stat <= 252",
    "P(KO) = P(roll >= remaining HP%)",
    "Trick Room: priority ~ -SPD",
    "boost_mult = (2+stage)/2, stage >= 0",
    "P(para|move) = 0.25 * BASE_SPD",
    "usage% = Sum(sets_i) / Sum(all teams)",
    "Choice_lock AND Protect = never both",
]
# (left%, top%, rotation deg, font-size px) — hand-placed, not random, so the
# layout is stable across reruns and doesn't collide with the centered
# content column.
_AMBIENT_POSITIONS = [
    (3, 8, -4, 11), (88, 6, 3, 11), (2, 32, 2, 10), (90, 28, -3, 10),
    (4, 58, -2, 11), (89, 55, 4, 10), (3, 82, 3, 11), (87, 80, -2, 10),
    (6, 96, -3, 10), (85, 95, 2, 10),
]


def _ambient_background_html() -> str:
    """A fixed, full-page, click-through layer of faint scattered
    equations, standing in for the Figma prototype's animated canvas
    (particles + a typewriter effect) — see ADR-026 for why the canvas
    itself (real JS, `requestAnimationFrame`) wasn't ported: script tags
    injected via st.markdown's HTML aren't reliably executed by Streamlit's
    frontend. This keeps the same "lab notebook" texture without depending
    on that. `pointer-events:none` and `z-index:0` so it never blocks a
    click and always paints behind the real page content that follows it
    in the DOM."""
    spans = "".join(
        f'<span style="position:absolute;left:{left}%;top:{top}%;'
        f"transform:rotate({rot}deg);font-family:var(--pvgc-font-mono);"
        f'font-size:{size}px;color:rgba(30,80,160,.16);white-space:nowrap;">'
        f"{html.escape(eq)}</span>"
        for eq, (left, top, rot, size) in zip(_AMBIENT_EQUATIONS, _AMBIENT_POSITIONS)
    )
    return (
        '<div style="position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;">'
        f"{spans}</div>"
    )


_TAG_PILLS = [("VGC Doubles", "var(--pvgc-green)"), ("Showdown Replay", "var(--pvgc-blue)"),
              ("Live Metagame", "var(--pvgc-gold)")]


def _hero_section_html() -> str:
    """The centered hero copy — icon, headline, subtitle, tag pills —
    ported from the Figma design's always-visible hero `<section>` (as
    opposed to its idle-only feature-card grid, see _feature_cards_html)."""
    tags = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;'
        f"border-radius:999px;background:{color}14;border:1px solid {color}40;"
        f'font-family:var(--pvgc-font-mono);font-size:.68rem;letter-spacing:.05em;color:{color};">'
        f'<span style="width:6px;height:6px;border-radius:50%;background:{color};"></span>'
        f"{html.escape(label)}</span>"
        for label, color in _TAG_PILLS
    )
    return f"""
<div style="position:relative;z-index:1;text-align:center;max-width:640px;margin:8px auto 30px;">
  <div style="margin-bottom:18px;">{_icon_html(_POKEBALL_ICON, size=104)}</div>
  <h1 style="font-family:var(--pvgc-font-display);font-weight:700;font-size:2.6rem;
             line-height:1.15;margin:0 0 16px;color:var(--pvgc-ink);">
    Understand every<br/>
    <em style="font-style:italic;background:linear-gradient(90deg,var(--pvgc-blue-dark) 0%,
               var(--pvgc-blue) 40%,var(--pvgc-green) 70%,var(--pvgc-blue-dark) 100%);
               background-size:200% auto;-webkit-background-clip:text;background-clip:text;
               -webkit-text-fill-color:transparent;">battle decision</em>
  </h1>
  <p style="font-family:var(--pvgc-font-body);font-size:1rem;line-height:1.6;
            color:var(--pvgc-ink-muted);margin:0 0 20px;">
    Paste a Pokémon Showdown replay (link, JSON, or raw log), ask a question in
    plain language — and receive a coach-level analysis grounded in real
    turn-by-turn damage calculations, speed tiers, and competitive metagame data.
  </p>
  <div style="display:flex;justify-content:center;flex-wrap:wrap;gap:10px;">{tags}</div>
</div>
"""


def _feature_icon_garchomp(size: int = 48) -> str:
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 48 48" fill="none" aria-hidden="true">
<ellipse cx="24" cy="30" rx="10" ry="12" fill="#4a6aaa" /><ellipse cx="24" cy="14" rx="10" ry="9" fill="#4a6aaa" />
<path d="M18 16 Q24 22 30 16" fill="#2a4a88" /><path d="M16 18 Q24 26 32 18" stroke="#2a4a88" stroke-width="1.5" fill="#f0c060" />
<path d="M10 18 L4 8 L14 14 Z" fill="#e05252" /><path d="M38 18 L44 8 L34 14 Z" fill="#e05252" />
<path d="M18 8 L24 2 L30 8" fill="#e05252" /><ellipse cx="20" cy="12" rx="2.5" ry="3" fill="#f0c060" />
<ellipse cx="28" cy="12" rx="2.5" ry="3" fill="#f0c060" /><circle cx="20" cy="12" r="1.3" fill="#1a1a1a" />
<circle cx="28" cy="12" r="1.3" fill="#1a1a1a" /><ellipse cx="24" cy="32" rx="7" ry="9" fill="#c8b4a0" />
<path d="M24 42 Q28 46 24 48 Q20 46 24 42Z" fill="#4a6aaa" /><path d="M14 26 L2 20 L12 32 Z" fill="#4a6aaa" />
<path d="M34 26 L46 20 L36 32 Z" fill="#4a6aaa" /></svg>"""


def _feature_icon_mewtwo(size: int = 48) -> str:
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 48 48" fill="none" aria-hidden="true">
<ellipse cx="24" cy="32" rx="10" ry="11" fill="#c8b4d4" /><ellipse cx="24" cy="16" rx="9" ry="8" fill="#c8b4d4" />
<circle cx="38" cy="26" r="4" fill="#c8b4d4" stroke="#9a80aa" stroke-width="1" />
<path d="M30 36 Q38 38 38 30" stroke="#9a80aa" stroke-width="2.5" fill="none" stroke-linecap="round" />
<ellipse cx="20" cy="15" rx="2.5" ry="3" fill="#6a3090" /><ellipse cx="28" cy="15" rx="2.5" ry="3" fill="#6a3090" />
<circle cx="21" cy="14" r="0.9" fill="white" /><circle cx="29" cy="14" r="0.9" fill="white" />
<path d="M19 9 Q24 7 29 9" stroke="#9a80aa" stroke-width="2" stroke-linecap="round" fill="none" />
<ellipse cx="24" cy="30" rx="6" ry="7" fill="#a090b8" />
<path d="M14 28 Q10 32 13 36" stroke="#c8b4d4" stroke-width="4" stroke-linecap="round" fill="none" />
<path d="M34 28 Q38 32 35 36" stroke="#c8b4d4" stroke-width="4" stroke-linecap="round" fill="none" />
<ellipse cx="19" cy="42" rx="4" ry="3" fill="#c8b4d4" /><ellipse cx="29" cy="42" rx="4" ry="3" fill="#c8b4d4" />
</svg>"""


def _feature_icon_pikachu(size: int = 48) -> str:
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 48 48" fill="none" aria-hidden="true">
<ellipse cx="24" cy="30" rx="12" ry="10" fill="#F6C823" /><ellipse cx="24" cy="18" rx="11" ry="10" fill="#F6C823" />
<path d="M13 10 L10 2 L17 6 Z" fill="#F6C823" stroke="#F6C823" stroke-width="1" /><path d="M13 10 L11 4 L16 7 Z" fill="#1a1a1a" />
<path d="M35 10 L38 2 L31 6 Z" fill="#F6C823" stroke="#F6C823" stroke-width="1" /><path d="M35 10 L37 4 L32 7 Z" fill="#1a1a1a" />
<circle cx="19" cy="17" r="2.5" fill="#1a1a1a" /><circle cx="29" cy="17" r="2.5" fill="#1a1a1a" />
<circle cx="20" cy="16" r="0.9" fill="white" /><circle cx="30" cy="16" r="0.9" fill="white" />
<ellipse cx="15" cy="21" rx="3.5" ry="2.5" fill="#e05252" opacity="0.7" /><ellipse cx="33" cy="21" rx="3.5" ry="2.5" fill="#e05252" opacity="0.7" />
<ellipse cx="24" cy="20" rx="1" ry="0.7" fill="#1a1a1a" />
<path d="M21 22 Q24 25 27 22" stroke="#1a1a1a" stroke-width="1.2" fill="none" stroke-linecap="round" />
<path d="M33 33 L40 26 L38 38 Z" fill="#F6C823" /><path d="M33 30 L41 24 L40 30 Z" fill="#1a1a1a" />
<path d="M17 27 Q24 26 31 27" stroke="#c8940a" stroke-width="1.8" stroke-linecap="round" />
<path d="M15 30 Q24 29 33 30" stroke="#c8940a" stroke-width="1.8" stroke-linecap="round" />
</svg>"""


# (icon builder, accent color token, title, description) — the three
# descriptions are accurate to this project's real deterministic/probabilistic
# pipeline (CLAUDE.md's own §1-2), not the prototype's generic mock copy.
_FEATURE_CARDS = [
    (_feature_icon_garchomp, "var(--pvgc-blue)", "Real Damage Calculations",
     "Damage and speed recalculated turn-by-turn using Smogon's actual "
     "@smogon/calc engine: Tailwind, Choice Scarf, stat boosts, Trick Room — "
     "never estimated by the LLM."),
    (_feature_icon_mewtwo, "var(--pvgc-purple)", "Grounded AI Coach",
     "The LLM narrates in plain language, but never contradicts the actual "
     "event order of the replay nor invents unconfirmed damage or abilities. "
     "The AI explains the truth — it never fabricates it."),
    (_feature_icon_pikachu, "var(--pvgc-gold)", "Live Metagame Context",
     "Probable sets, threats, and synergies drawn from real competitive "
     "usage statistics — Smogon Chaos and the official dex — not from "
     "model guesswork."),
]


def _feature_cards_html() -> str:
    cards = "".join(
        f'<div style="background:{color}0f;border:1px solid {color}29;border-radius:16px;'
        f'padding:20px;backdrop-filter:blur(12px);">'
        f'<div style="margin-bottom:12px;">{icon_fn(48)}</div>'
        f'<h3 style="font-family:var(--pvgc-font-display);font-weight:700;font-size:.95rem;'
        f'margin:0 0 8px;color:{color};">{html.escape(title)}</h3>'
        f'<p style="font-family:var(--pvgc-font-body);font-size:.82rem;line-height:1.55;'
        f'margin:0;color:var(--pvgc-ink-muted);">{html.escape(desc)}</p></div>'
        for icon_fn, color, title, desc in _FEATURE_CARDS
    )
    return (
        '<div style="position:relative;z-index:1;display:grid;'
        "grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;"
        f'max-width:900px;margin:36px auto 0;">{cards}</div>'
    )


def _grass_row_html(count: int = 20) -> str:
    """A row of small decorative grass blades under the input card, ported
    from the Figma design's GrassRow — its per-blade jitter was client-side
    Math.random(); reproduced here with a locally-seeded Random so the
    layout is stable across Streamlit reruns instead of jittering on every
    widget interaction."""
    rng = random.Random(42)
    blades = []
    for i in range(count):
        x_pct = (i / count) * 100 + (rng.random() - 0.5) * 3
        h = 18 + rng.random() * 22
        delay = rng.random() * 2.5
        dur = 3 + rng.random() * 1.5
        blades.append(
            f'<div style="position:absolute;bottom:0;left:{x_pct:.2f}%;'
            f'animation:pvgc-float {dur:.2f}s ease-in-out {delay:.2f}s infinite;">'
            f'<svg width="8" height="{h:.0f}" viewBox="0 0 8 {h:.0f}" fill="none">'
            f'<path d="M4 {h:.0f} Q1 {h * 0.45:.0f} 4 0" stroke="rgba(37,99,168,.35)" '
            f'stroke-width="1.5" stroke-linecap="round" /></svg></div>'
        )
    return f'<div style="position:relative;height:40px;overflow:hidden;">{"".join(blades)}</div>'


def _footer_html() -> str:
    """A minimal footer, ported from the Figma design — the same fan-tool
    disclaimer this project already carries elsewhere, in the new
    typography."""
    return f"""
<div style="margin-top:40px;padding:18px 2px 4px;border-top:1px solid rgba(37,99,168,.12);
            display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
  <div style="display:flex;align-items:center;gap:8px;">
    {_icon_html(_POKEBALL_ICON, size=16)}
    <span style="font-family:var(--pvgc-font-display);font-size:.85rem;
                 color:var(--pvgc-ink-muted);">ProfessorVGC</span>
  </div>
  <span style="font-family:var(--pvgc-font-mono);font-size:.58rem;letter-spacing:.08em;
               color:var(--pvgc-ink-muted);opacity:.75;">
    UNOFFICIAL FAN TOOL &middot; NOT AFFILIATED WITH NINTENDO / GAME FREAK / THE POKEMON COMPANY
  </span>
</div>
"""


def main() -> None:
    st.set_page_config(page_title="ProfessorVGC", page_icon=_POKEBALL_ICON, layout="wide")
    _inject_global_styles()
    st.markdown(_ambient_background_html(), unsafe_allow_html=True)
    st.markdown(_hero_header_html(), unsafe_allow_html=True)
    st.caption("Deterministic damage-calc + Chaos metagame stats + LLM explainability.")

    # The marketing hero copy/tag pills and feature-card grid (see
    # _hero_section_html/_feature_cards_html) only make sense before there's
    # a real analysis to show — matching the Figma design's own
    # `phase === 'idle'`-gated sections. Once a replay has been analyzed,
    # showing them again would just push the real Answer/battle panel
    # further down the page on every rerun for no benefit.
    is_idle = "last_replay" not in st.session_state and "last_result" not in st.session_state
    if is_idle:
        st.markdown(_hero_section_html(), unsafe_allow_html=True)

    with st.sidebar:
        st.header("Configuration (BYOK)")
        provider = st.selectbox("LLM provider", ["gemini", "openai"], index=0)
        orchestrator = st.selectbox(
            "Orchestration", ["adk", "langchain", "native"], index=0,
            help="Google ADK agents (default), LangChain LCEL chains, or the "
                 "hand-rolled native pipeline.",
        )
        st.info(
            f"{_icon_md(_POKEBALL_ICON)} Set your key via environment variables:\n"
            "`PROFESSORVGC_OPENAI_API_KEY` or `PROFESSORVGC_GEMINI_API_KEY`."
        )
        if st.button("Reset conversation"):
            # Also clears the battle panel's own state (last_replay/
            # last_result/last_error/turn_index) — st.session_state persists
            # across a code-reload rerun (that's its job), so a stale object
            # from before a schema change (e.g. an older ReplayPokemonState
            # missing a newly-added field like `boosts`) can otherwise
            # survive a `git pull` + hot-reload and crash on next render.
            # This button is the in-app recovery for that; a full restart of
            # `streamlit run` plus a fresh browser tab clears it too.
            for key in (
                "session_id", "container", "last_replay", "last_result",
                "last_error", "turn_index",
            ):
                st.session_state.pop(key, None)
            st.rerun()

        # Optional background music — see src/ui/assets/audio/README.md.
        # Renders nothing at all when the folder is empty (the default).
        _render_background_music()

    container = _get_container()

    st.subheader("1 · Replay")
    replay_text = st.text_area(
        "Paste the Showdown replay JSON, raw battle log, or a replay URL",
        height=200,
        placeholder=(
            '{"format": "gen9vgc2025", "sides": [...]}  — or raw |...| log  — or '
            "https://play.pokemonshowdown.com/battle-gen9vgc2025regh-1234567890"
        ),
    )

    st.subheader("2 · Question")
    question = st.text_input(
        "Ask ProfessorVGC",
        placeholder="e.g. Does my Garchomp OHKO their Sinistcha? What's the safe swap?",
    )

    analyze_clicked = st.button("Analyze", type="primary")
    if is_idle:
        st.markdown(_grass_row_html(), unsafe_allow_html=True)
        st.markdown(_feature_cards_html(), unsafe_allow_html=True)

    if analyze_clicked:
        if not replay_text.strip() and not question.strip():
            st.warning("Provide a replay and/or a question.")
        else:
            # Big centered spinner, shown for the ENTIRE window below (parse
            # + LLM pipeline + sprite pre-warm), via st.empty() so it can be
            # cleared as one call right before the results render — not
            # st.spinner()'s small inline text, which only ever covered the
            # pipeline.analyze() call and left the (often slower, on a first
            # view of new species — see the pre-warm note below) battle-panel
            # rendering that follows with no "still working" indicator of its
            # own. Reported: the stepper/battle panel visibly became ready
            # before the Answer text did, which read as the app being done
            # when it wasn't — this closes that gap from both ends: nothing
            # in the results area renders until this whole block finishes
            # (unchanged), and now nothing NEW network-bound happens after
            # the overlay clears either.
            loading = st.empty()
            loading.markdown(_loading_overlay_html(), unsafe_allow_html=True)
            try:
                # If the pasted text is a recognized Showdown replay URL
                # (play.pokemonshowdown.com/battle-<id> or
                # replay.pokemonshowdown.com/<id>[.json] — the two shapes a
                # user would actually copy/paste), fetch its JSON now, still
                # inside the loading overlay's window, and use THAT as the
                # replay content for everything below instead of the raw
                # pasted URL text (which would just fail to parse as a
                # replay). Anything that isn't a recognized URL — pasted
                # JSON or raw log text — passes through unchanged; this
                # never misidentifies replay content itself as a URL.
                resolved_text = replay_text.strip()
                fetch_url = normalize_replay_json_url(resolved_text) if resolved_text else None
                fetch_error: ReplayFetchError | None = None
                if fetch_url is not None:
                    try:
                        resolved_text = fetch_replay_json(fetch_url)
                    except ReplayFetchError as exc:
                        fetch_error = exc

                if fetch_error is not None:
                    st.session_state["last_replay"] = BattleReplay()
                    st.session_state["last_result"] = None
                    st.session_state["last_error"] = fetch_error
                else:
                    # Parsed independently of, and BEFORE, the LLM pipeline
                    # call below — a second, unrelated parse of the same
                    # resolved text, purely for the visual panel (see
                    # replay_viewer_parser's module docstring for why these
                    # are kept fully decoupled rather than sharing one
                    # parse). A parse failure here must never affect the
                    # LLM call.
                    replay = BattleReplay()
                    if resolved_text:
                        try:
                            replay = parse_replay_for_viewer(resolved_text)
                        except Exception:  # noqa: BLE001 - this panel is best-effort only
                            replay = BattleReplay()
                    st.session_state["last_replay"] = replay
                    # Reset the stepper to the first turn (Leads) for a
                    # freshly analyzed battle, rather than leaving it
                    # wherever it was left on a previous, unrelated replay.
                    turn_index = 0
                    st.session_state["turn_index"] = turn_index

                    request = AnalysisRequest(
                        session_id=_session_id(),
                        replay_raw_text=resolved_text or None,
                        question=question.strip(),
                        provider=provider,
                    )
                    try:
                        pipeline = container.build_pipeline(provider, orchestrator)
                        st.session_state["last_result"] = pipeline.analyze(request)
                        st.session_state["last_error"] = None
                    except ProfessorVGCError as exc:
                        st.session_state["last_result"] = None
                        st.session_state["last_error"] = exc

                    # Pre-warm the sprite-reachability cache (st.cache_data,
                    # keyed by URL — see ADR-015's follow-up) for exactly the
                    # turn that's about to render, while the overlay is
                    # still up. Without this, a replay with species never
                    # seen before in this server process would do its
                    # first-ever sprite HEAD checks AFTER the overlay
                    # clears, in the render block below — invisible latency
                    # with no spinner covering it, which is the concrete
                    # mechanism behind the reported stagger. The result is
                    # discarded; this call exists only for its caching side
                    # effect, so the real render moments later hits 100%
                    # cache and paints effectively instantly.
                    if replay.snapshots:
                        _battle_stage_html(replay, replay.snapshots[turn_index])
            finally:
                loading.empty()

    # Rendered from session_state, OUTSIDE the button's own click branch —
    # Streamlit reruns the whole script on every widget interaction (e.g. the
    # battle panel's own stepper buttons/slider below), and `st.button(...)`
    # only evaluates True on the exact run it was clicked. Tying this render
    # to that branch would make the entire result area (including the panel
    # the stepper itself belongs to) vanish the moment the stepper was used.
    if "last_replay" in st.session_state or "last_result" in st.session_state:
        left, main_col = st.columns([1, 2])
        with left:
            replay = st.session_state.get("last_replay") or BattleReplay()
            if replay.snapshots:
                _render_battle_panel(replay)
            else:
                st.caption("No battle replay to visualize for this input.")

        with main_col:
            error = st.session_state.get("last_error")
            result = st.session_state.get("last_result")
            if error is not None:
                # Show the specific failure category plus the detailed message
                # so the user can tell a bad replay from a missing key or a
                # calc issue.
                st.error(f"Analysis failed — {type(error).__name__}: {error}")
                st.caption(_error_tip(error))
                return
            if result is None:
                return

            # Which in-game turn the stepper is currently on — used below to
            # highlight the matching slice of the Answer, and the matching
            # turn-by-turn/protect-read entries, so the slider visually ties
            # the narrative to the battle state it's showing.
            current_turn = _current_turn_number(replay)

            st.subheader("Answer")
            st.markdown(_highlight_answer_by_turn(result.answer, current_turn))

            if result.agent_tool_calls:
                # ADR-028: only the LangChain backend's explanation agent can
                # reach back into damage_calc/chaos_meta_stats/smogon_strategy
                # mid-answer, for a question the precomputed context above
                # didn't already cover (a hypothetical item, a different
                # tier, ...). Flag it plainly whenever it happened, since
                # those figures are fresh/hypothetical lookups, not part of
                # the precomputed ground truth for the real game.
                failed = [c for c in result.agent_tool_calls if not c.ok]
                tool_names = ", ".join(sorted({c.tool for c in result.agent_tool_calls}))
                count = len(result.agent_tool_calls)
                banner = st.warning if failed else st.info
                icon = _PIKACHU_ICON if failed else _POKEBALL_ICON
                banner(
                    f"{_icon_md(icon)} The AI reached back into live data mid-answer — "
                    f"{count} on-demand lookup{'s' if count != 1 else ''} ({tool_names}) "
                    "beyond the precomputed ground truth above. This only happens on the "
                    "LangChain backend, for questions that ground truth didn't already cover."
                    + (f" {len(failed)} lookup{'s' if len(failed) != 1 else ''} failed." if failed else "")
                )
                with st.expander(f"On-demand agent lookups ({count})"):
                    for call in result.agent_tool_calls:
                        status = "ok" if call.ok else "failed"
                        st.markdown(f"**{call.tool}** — {status}")
                        st.caption(f"args: {call.arguments}")
                        if call.summary:
                            st.caption(call.summary)

            if result.battle_result:
                with st.expander("Battle result (from the replay log)", expanded=True):
                    st.text(result.battle_result)

            if not any(s.top_moves for s in result.meta_context.pokemon_stats.values()):
                st.info(
                    f"{_icon_md(_POKEBALL_ICON)} No metagame (Chaos) data was loaded for "
                    "these Pokemon, so the Smogon strategy section is empty. Point "
                    "`PROFESSORVGC_CHAOS_DATA_PATH` at a Chaos dump that covers this format — "
                    "see DATA.md."
                )

            with st.expander(
                f"Deterministic verdicts — spotlight matchups ({len(result.verdicts)})"
            ):
                st.caption(
                    "Supplementary hand-picked matchups (not necessarily ones that "
                    "occurred) — see 'Turn-by-turn checks' below for the exhaustive, "
                    "ordered ground truth covering every real action of the game."
                )
                if not result.verdicts:
                    st.caption("(none selected for this question)")
                for verdict in result.verdicts:
                    dmg = verdict.best_damage
                    st.markdown(
                        f"**{verdict.attacker} → {verdict.defender}** using "
                        f"*{verdict.best_move}*: {dmg.min_percent}%–{dmg.max_percent}% "
                        f"({dmg.ko_chance_text or 'n/a'})"
                    )
                    st.caption(dmg.description or "(no spread/nature detail returned)")
                    if verdict.speed:
                        sp = verdict.speed
                        tie = " (speed tie)" if sp.is_tie else ""
                        conds = f" — {', '.join(sp.conditions)}" if sp.conditions else ""
                        st.caption(
                            f"Moves first: {sp.faster} {sp.faster_speed} "
                            f"> {sp.slower} {sp.slower_speed}{tie}{conds}"
                        )
                    if verdict.stat_caveat:
                        st.warning(f"{_icon_md(_PIKACHU_ICON)} {verdict.stat_caveat}")

            if result.turn_checks:
                with st.expander(
                    f"Turn-by-turn checks — every real action, in order ({len(result.turn_checks)})",
                    expanded=True,
                ):
                    st.caption(
                        "The exhaustive, ordered ground-truth feedback loop: the engine "
                        "re-consulted for the move actually used AND for every other "
                        "confirmed move that Pokemon knew this game, turn by turn."
                    )
                    for tc in result.turn_checks:
                        header = f"T{tc.turn} · {tc.actor} used {tc.move}"
                        if tc.conditions:
                            header += f" — {', '.join(tc.conditions)}"
                        header = f"**{header}**"
                        if tc.turn == current_turn:
                            header = f":orange-background[{header}]"
                        st.markdown(header)
                        for d in tc.damage_checks:
                            st.caption(
                                f"{d.target}: projected {d.projected_min_percent}–"
                                f"{d.projected_max_percent}% ({d.projected_ko_text or 'n/a'}) "
                                f"| actual: {d.actual_result or 'n/a'}"
                            )
                            if d.description:
                                st.caption(f"   {d.description}")
                        if tc.speed:
                            c = f" — {', '.join(tc.speed.conditions)}" if tc.speed.conditions else ""
                            st.caption(f"speed: {tc.speed.faster} moves first{c}")
                        if tc.best_alternatives:
                            st.markdown("_Best available plays this turn (ranked):_")
                            for i, alt in enumerate(tc.best_alternatives, start=1):
                                marker = f" {_icon_html(_PIKACHU_ICON, size=14)}" if alt.move == tc.move else ""
                                st.caption(
                                    f"{i}. {alt.move} vs {alt.target}: "
                                    f"{alt.min_percent}%–{alt.max_percent}% "
                                    f"({alt.ko_chance_text or 'n/a'}){marker}",
                                    unsafe_allow_html=True,
                                )
                        if tc.stat_caveat:
                            st.warning(f"{_icon_md(_PIKACHU_ICON)} {tc.stat_caveat}")
                        if tc.note:
                            st.caption(tc.note)

            if result.protect_reads:
                with st.expander(
                    f"Protect reads — precomputed spread/misallocation classification ({len(result.protect_reads)})"
                ):
                    st.caption(
                        "One entry per Protect-family block, already classified so the "
                        "explanation only has to report it: spread moves are "
                        "protect-resistant (not a read); a genuine single-target read "
                        "that denied no real threat while a teammate fainted the same "
                        "turn is flagged misallocated."
                    )
                    for pr in result.protect_reads:
                        kind = "genuine read" if pr.is_genuine_read else "spread (not a read)"
                        pr_header = (
                            f"**T{pr.turn} · {pr.blocker} blocked {pr.attacker}'s "
                            f"{pr.move}** — {kind}"
                        )
                        if pr.turn == current_turn:
                            pr_header = f":orange-background[{pr_header}]"
                        st.markdown(pr_header)
                        d = pr.value_denied
                        st.caption(
                            f"Denied: {d.projected_min_percent}–{d.projected_max_percent}% "
                            f"({d.projected_ko_text or 'n/a'})"
                        )
                        if pr.other_targets_hit:
                            others = ", ".join(
                                f"{o.target} {o.projected_min_percent}–{o.projected_max_percent}%"
                                for o in pr.other_targets_hit
                            )
                            st.caption(f"Also hit that turn regardless: {others}")
                        if pr.misallocated:
                            st.warning(
                                f"{_icon_md(_PIKACHU_ICON)} Misallocated: no immediate KO "
                                f"threat, but {pr.teammate_fainted} fainted the same turn."
                            )
                        elif pr.was_immediate_ko_threat:
                            st.caption("Justified: denied a same-turn OHKO chance.")

            with st.expander("Selection plan (1st AI)"):
                st.json(result.selection.model_dump())
            with st.expander("Metagame context (Chaos)"):
                st.json(result.meta_context.model_dump())
            with st.expander("Strategies (Smogon-derived)"):
                st.json([s.model_dump() for s in result.strategies])

    st.markdown(_footer_html(), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
