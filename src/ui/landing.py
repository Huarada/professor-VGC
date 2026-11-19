"""Landing-page sections: brand header, concise introduction, features, footer."""

from __future__ import annotations

from src.ui.icons import POKEBALL_ICON, icon_html


def hero_header_html() -> str:
    """Top identity bar: logo, name and an "AI ready" dot (normal flow, not sticky)."""
    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
            gap:10px;padding:2px 2px 16px;border-bottom:1px solid rgba(37,99,168,.14);
            margin-bottom:20px;">
  <div style="display:flex;align-items:center;gap:12px;">
    {icon_html(POKEBALL_ICON, size=34)}
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


# Introductory copy sits directly above the replay form on the idle page.


def hero_section_html() -> str:
    """A short explanation that leaves the replay input visible above the fold."""
    return """
<div class="pvgc-hero">
  <div class="pvgc-hero-copy">
  <p style="font-family:var(--pvgc-font-body);font-size:.78rem;font-weight:700;
            letter-spacing:.06em;text-transform:uppercase;color:var(--pvgc-blue);margin:0 0 6px;">
    Pokémon Champions VGC
  </p>
  <h1 style="font-family:var(--pvgc-font-display);font-weight:700;font-size:2rem;
             line-height:1.2;margin:0 0 8px;color:var(--pvgc-ink);">
    Review the decisions behind the result
  </h1>
  <p style="font-family:var(--pvgc-font-body);font-size:.98rem;line-height:1.55;
            color:var(--pvgc-ink-muted);margin:0;">
    Add a Showdown replay and ask a question. ProfessorVGC checks battle events
    and damage calculations before explaining the game or BO3 series.
  </p>
  </div>
  <div class="pvgc-hero-art" aria-hidden="true">
    <img class="pvgc-hero-sprite pvgc-hero-pikachu"
         src="https://play.pokemonshowdown.com/sprites/gen5ani/pikachu.gif"
         alt="" width="112" height="112" decoding="async">
    <img class="pvgc-hero-sprite pvgc-hero-mewtwo"
         src="https://play.pokemonshowdown.com/sprites/gen5ani/mewtwo.gif"
         alt="" width="128" height="128" decoding="async">
  </div>
</div>
"""


def footer_html() -> str:
    """A minimal footer with the fan-tool disclaimer."""
    return f"""
<div style="margin-top:40px;padding:18px 2px 4px;border-top:1px solid rgba(37,99,168,.12);
            display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
  <div style="display:flex;align-items:center;gap:8px;">
    {icon_html(POKEBALL_ICON, size=16)}
    <span style="font-family:var(--pvgc-font-display);font-size:.85rem;
                 color:var(--pvgc-ink-muted);">ProfessorVGC</span>
  </div>
  <span style="font-family:var(--pvgc-font-mono);font-size:.58rem;letter-spacing:.08em;
               color:var(--pvgc-ink-muted);opacity:.75;">
    UNOFFICIAL FAN TOOL &middot; NOT AFFILIATED WITH NINTENDO / GAME FREAK / THE POKEMON COMPANY
  </span>
</div>
"""
