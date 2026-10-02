"""Global look of the Streamlit app: design tokens, component theme and backgrounds."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import streamlit as st


# ---------------------------------------------------------------------------
# Background images — dedicated drop folder: src/ui/assets/backgrounds/
# (see the README.md in that folder). Drop a `page.<ext>` file there for the
# whole-app wallpaper and/or a `battle-stage.<ext>` file for the battle
# panel's own backdrop — jpg/jpeg/png/webp, any resolution, picked up
# automatically on the next page load, no code edit needed. Nobody has
# dropped one in yet is the default, working state, not an error: the
# pure-CSS gradients below (LAB_BACKGROUND_CSS_DEFAULT /
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
LAB_BACKGROUND_CSS_DEFAULT = (
    "radial-gradient(ellipse 60% 42% at 50% 0%, rgba(150,225,255,.22), transparent 70%),"
    "repeating-linear-gradient(90deg, rgba(120,210,255,.06) 0px, rgba(120,210,255,.06) 2px,"
    " transparent 2px, transparent 64px),"
    "linear-gradient(180deg, #0a1e24 0%, #123241 52%, #0a2129 100%)"
)


# PAGE-WIDE background — the whole app's backdrop (distinct from
# LAB_BACKGROUND_CSS_DEFAULT above, which only skins the battle-panel
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
# battle-panel HTML (battle_stage_html/hp_box_html/etc.) — that stays on
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
    on `battle_panel.http_head_ok`, which hit exactly this bug first."""
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


def background_css(stem: str, default_gradient: str, scrim: str = _DARK_SCRIM_CSS) -> str:
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


def inject_global_styles() -> None:
    """Paints the page-wide lab wallpaper onto Streamlit's own scrollable
    app container (its stable `data-testid` hooks, not `body` — Streamlit
    scrolls an inner container, not the page body itself), with
    `background-attachment: fixed` for a parallax feel: the wallpaper stays
    put in the viewport while the Q&A/analysis content scrolls over it, so
    it visibly "lags behind" — lowers into view — as the page scrolls down.
    Uses a user-dropped image from src/ui/assets/backgrounds/page.<ext> if
    present, else the built-in gradient — see background_css."""
    page_background = background_css("page", _PAGE_BACKGROUND_CSS_DEFAULT, _LIGHT_SCRIM_CSS)
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
