"""Small inline icons shared by every UI module."""

from __future__ import annotations

from urllib.parse import quote


# Site-wide icons, no emoji: inline SVG data URIs. A pokéball for branding and
# notes; a Pikachu face for noteworthy moments (warnings, the play taken).
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
    """A sized icon for HTML contexts. A <span> with a background image, not an
    <img>: Streamlit caps every markdown <img> at ``max-height: 1em``.
    """
    return (
        f'<span role="img" aria-label="" style="display:inline-block;'
        f"width:{size}px;height:{size}px;flex-shrink:0;vertical-align:middle;"
        f"background-image:url('{uri}');background-size:contain;"
        f'background-repeat:no-repeat;background-position:center;"></span>'
    )
