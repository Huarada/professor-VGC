"""Global look of the Streamlit app: design tokens, component theme and backgrounds."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import streamlit as st

# Optional background images: drop page.<ext> / battle-stage.<ext> into
# src/ui/assets/backgrounds/. Without them the CSS gradients below are used.
_BACKGROUNDS_DIR = Path(__file__).resolve().parent / "assets" / "backgrounds"


_BACKGROUND_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


# Battle stage backdrop: a "genetics lab" gradient (a gradient can never 404).
LAB_BACKGROUND_CSS_DEFAULT = (
    "radial-gradient(ellipse 60% 42% at 50% 0%, rgba(150,225,255,.22), transparent 70%),"
    "repeating-linear-gradient(90deg, rgba(120,210,255,.06) 0px, rgba(120,210,255,.06) 2px,"
    " transparent 2px, transparent 64px),"
    "linear-gradient(180deg, #0a1e24 0%, #123241 52%, #0a2129 100%)"
)


# A quiet page surface keeps the replay form and evidence readable.
_PAGE_BACKGROUND_CSS_DEFAULT = (
    "linear-gradient(180deg, #f7f9fc 0%, #edf3f9 100%)"
)


# Design tokens from the Figma prototype: Lora (headings), Nunito (body),
# Space Mono (labels/data) and the accent palette, as CSS custom properties.
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


# Applies the tokens to Streamlit's own widgets via data-testid hooks; the
# battle panel keeps its own dark Showdown look.
_COMPONENT_THEME_CSS = """
html, body, [data-testid="stAppViewContainer"], .stApp {
    font-family: var(--pvgc-font-body);
}
.pvgc-hero {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    margin: 18px 2px 22px;
}
.pvgc-hero-copy {
    max-width: 760px;
    min-width: 0;
}
.pvgc-hero-art {
    position: relative;
    flex: 0 0 248px;
    height: 144px;
    pointer-events: none;
}
.pvgc-hero-art::before {
    content: '';
    position: absolute;
    inset: 15px 10px 7px;
    border: 1px solid rgba(37, 99, 168, .13);
    border-radius: 50%;
    background: radial-gradient(ellipse, rgba(194, 221, 246, .48), transparent 70%);
}
.pvgc-hero-sprite {
    position: absolute;
    display: block;
    object-fit: contain;
    image-rendering: pixelated;
    filter: drop-shadow(0 6px 5px rgba(30, 42, 74, .12));
}
.pvgc-hero-pikachu {
    left: 13px;
    bottom: 7px;
    width: 112px;
    height: 112px;
}
.pvgc-hero-mewtwo {
    right: 4px;
    bottom: 12px;
    width: 128px;
    height: 128px;
}
@media (max-width: 850px), (prefers-reduced-motion: reduce) {
    .pvgc-hero-art { display: none; }
}
h1, h2, h3, h4,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
    font-family: var(--pvgc-font-display) !important;
    color: var(--pvgc-ink);
}
[data-testid="stCaptionContainer"] {
    font-family: var(--pvgc-font-body);
}
[data-testid="stWidgetLabel"] p {
    font-family: var(--pvgc-font-body) !important;
    font-size: .86rem !important;
    font-weight: 700;
    color: var(--pvgc-ink-muted) !important;
}
[data-testid="stExpander"], [data-testid="stVerticalBlockBorderWrapper"] {
    background: #fff !important;
    border: 1px solid rgba(37, 99, 168, .14) !important;
    border-radius: 12px !important;
}
[data-testid="stExpander"] summary {
    font-family: var(--pvgc-font-display);
    color: var(--pvgc-ink);
}
[data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input {
    border-radius: 10px !important;
    border: 1.5px solid rgba(37, 99, 168, .16) !important;
    background: #fff !important;
}
.stButton button[kind="primary"] {
    background: var(--pvgc-blue-dark) !important;
    border: none !important;
    border-radius: 10px !important;
    font-family: var(--pvgc-font-body);
    font-weight: 700 !important;
}
.stButton button[kind="secondary"] {
    border-radius: 10px !important;
    border-color: rgba(37, 99, 168, .25) !important;
    color: var(--pvgc-blue) !important;
}
"""


def _find_background_image(stem: str) -> Path | None:
    """The dropped ``src/ui/assets/backgrounds/<stem>.<ext>`` image, or None."""
    for ext in _BACKGROUND_IMAGE_EXTENSIONS:
        candidate = _BACKGROUNDS_DIR / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


@st.cache_data(show_spinner=False)
def _image_data_uri(path_str: str, mtime_ns: int) -> str:
    """A local image as a data: URI, cached with st.cache_data by path + mtime."""
    path = Path(path_str)
    mime, _ = mimetypes.guess_type(path.name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


# Scrims over a dropped image: dark for the battle stage, light for the page.
_DARK_SCRIM_CSS = "linear-gradient(rgba(7,14,12,.72), rgba(7,14,12,.88))"


_LIGHT_SCRIM_CSS = "linear-gradient(rgba(219,238,255,.8), rgba(200,228,248,.9))"


def background_css(stem: str, default_gradient: str, scrim: str = _DARK_SCRIM_CSS) -> str:
    """CSS ``background`` for a backdrop: the dropped image under its scrim, else
    the built-in gradient.
    """
    image_path = _find_background_image(stem)
    if image_path is None:
        return default_gradient
    data_uri = _image_data_uri(str(image_path), image_path.stat().st_mtime_ns)
    return f"{scrim}, url('{data_uri}')"


def inject_global_styles() -> None:
    """Inject the theme and the fixed (parallax) page backdrop on Streamlit's
    scrolling app container.
    """
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
