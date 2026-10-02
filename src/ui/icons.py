"""Small inline icons shared by every UI module."""

from __future__ import annotations

from urllib.parse import quote


# The site-wide icon set: no emoji anywhere on the page. Two self-contained,
# hand-drawn SVG glyphs (inline data URIs, zero network dependency — the
# same reliability principle behind the sprite HEAD-check above) stand in
# for every emoji the page used to carry. A colored pokéball marks branding/
# neutral chrome (page icon, title, plain info notes); a minimalist Pikachu
# face marks a "noteworthy" moment — a caveat/warning, or the play that was
# actually taken. Explicit width/height on the <svg> root means both render
# small out of the box even through plain Markdown image syntax (no HTML
# needed), since `st.info`/`st.warning` bodies are Markdown-only.
POKEBALL_ICON = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="18" height="18">'
    '<circle cx="32" cy="32" r="29" fill="#fff" stroke="#222" stroke-width="3"/>'
    '<path d="M3 32a29 29 0 0 1 58 0z" fill="#ee1515" stroke="#222" stroke-width="3"/>'
    '<path d="M3 32h22M39 32h22" stroke="#222" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="9" fill="#fff" stroke="#222" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="3.5" fill="#222"/>'
    "</svg>"
)


PIKACHU_ICON = "data:image/svg+xml," + quote(
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


def icon_md(uri: str) -> str:
    """Markdown-image form of an icon, for use inside st.info/st.warning
    bodies — those render plain Markdown, not raw HTML, so an <img> tag
    would be escaped rather than displayed."""
    return f"![]({uri})"


def icon_html(uri: str, size: int = 18) -> str:
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
    regardless of how that cascade war would have resolved. `icon_md`'s
    small inline warning/info icons go through Streamlit's native
    Markdown image syntax (a real `<img>`) instead of this function and
    are unaffected — that ~1em cap is correct, wanted behavior there."""
    return (
        f'<span role="img" aria-label="" style="display:inline-block;'
        f"width:{size}px;height:{size}px;flex-shrink:0;vertical-align:middle;"
        f"background-image:url('{uri}');background-size:contain;"
        f'background-repeat:no-repeat;background-position:center;"></span>'
    )
