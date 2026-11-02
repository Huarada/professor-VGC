"""The full-screen loading overlay shown while an analysis runs."""

from __future__ import annotations

import html
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from src.domain.interfaces import ProgressReporter
from src.ui.battle_panel import PLACEHOLDER_SPRITE

# The loading overlay's rotating captions, ported from the Figma design's
# LoadingScreen component (originally driven by a JS setInterval). Reproduced
# here as pure CSS keyframes instead — see loading_overlay_html — since
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


def loading_overlay_html(*, title: str = "Analysing your battle", steps: Sequence[str] | None = None) -> str:
    """A big, centered, spinning pokeball covering the whole viewport, with
    a pulsing ring, rotating status captions and progress dots underneath —
    the explicit "still processing" indicator for the full duration of an
    analysis, so it's never ambiguous whether the app is done or still
    working. Reuses PLACEHOLDER_SPRITE's black-and-white pokeball (the
    same shape already used elsewhere in this file for a missing sprite)
    rather than the branded red/white POKEBALL_ICON — deliberately
    monochrome, as asked for, not colored. The captions/dots below cycle via
    the CSS-only staggered-animation trick documented on _LOADING_STEPS:
    every span shares one keyframe/duration and differs only by
    animation-delay, so they take turns being visible without any JS."""
    captions = steps or _LOADING_STEPS
    n = len(captions)
    total = n * _LOADING_STEP_SECONDS
    window_pct = 100 / n
    caption_spans = "".join(
        f'<span style="position:absolute;inset:0;display:flex;align-items:center;'
        f'justify-content:center;opacity:0;text-align:center;padding:0 12px;'
        f'animation:pvgc-step-fade {total}s ease-in-out {i * _LOADING_STEP_SECONDS}s infinite;">'
        f"{html.escape(step)}</span>"
        for i, step in enumerate(captions)
    )
    dot_spans = "".join(
        f'<span style="display:inline-block;height:6px;border-radius:999px;'
        f"background:rgba(37,99,168,.22);margin:0 3px;"
        f'animation:pvgc-dot-active {total}s steps(1) {i * _LOADING_STEP_SECONDS}s infinite;"></span>'
        for i in range(n)
    )
    if n == 1:
        # Real stage callbacks replace this text; keep it readable throughout
        # long calculator/model calls instead of blinking a single CSS caption.
        caption_spans = f'<span data-live-stage="true">{html.escape(captions[0])}</span>'
        dot_spans = '<span style="display:inline-block;width:22px;height:6px;border-radius:999px;background:#2563a8;"></span>'
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
      <img src="{PLACEHOLDER_SPRITE}" alt="Loading"
           style="width:90px;height:90px;animation:pvgc-spin 1s linear infinite;
                  filter:drop-shadow(0 4px 18px rgba(0,0,0,.3));" />
    </div>
    <div style="text-align:center;">
      <p style="font-family:'Lora',Georgia,serif;font-style:italic;font-weight:600;
                font-size:1.25rem;color:#1e3a8a;margin:0 0 10px;">
        {html.escape(title)}
      </p>
      <div role="status" aria-live="polite" style="position:relative;height:{'auto' if n == 1 else '3.3rem'};
                  min-height:3.3rem;width:min(640px,calc(100vw - 40px));line-height:1.65;overflow-wrap:anywhere;
                  font-family:'Space Mono',monospace;font-size:.8rem;letter-spacing:.03em;
                  color:rgba(30,58,138,.8);">
        {caption_spans}
      </div>
    </div>
    <div>{dot_spans}</div>
  </div>
</div>
"""


@contextmanager
def loading_overlay(*, title: str, steps: Sequence[str]) -> Iterator[ProgressReporter]:
    """Reuse the monochrome Pokeball and always remove it after success or error."""
    import streamlit as st

    placeholder = st.empty()

    def update(message: str) -> None:
        placeholder.markdown(loading_overlay_html(title=title, steps=(message,)), unsafe_allow_html=True)

    try:
        placeholder.markdown(loading_overlay_html(title=title, steps=steps), unsafe_allow_html=True)
        yield update
    finally:
        placeholder.empty()
