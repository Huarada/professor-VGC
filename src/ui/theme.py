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


# Page-wide backdrop: a light "battle notebook" sky with a graph-paper grid,
# from the Figma prototype (ADR-026). The battle stage stays dark.
_PAGE_BACKGROUND_CSS_DEFAULT = (
    "radial-gradient(ellipse 70% 40% at 50% 0%, rgba(255,255,255,.55), transparent 70%),"
    "repeating-linear-gradient(0deg, rgba(37,99,168,.05) 0px, rgba(37,99,168,.05) 1px,"
    " transparent 1px, transparent 46px),"
    "repeating-linear-gradient(90deg, rgba(37,99,168,.05) 0px, rgba(37,99,168,.05) 1px,"
    " transparent 1px, transparent 46px),"
    "linear-gradient(160deg, #dbeeff 0%, #c8e4f8 35%, #b8d8f4 70%, #cce7fa 100%)"
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
