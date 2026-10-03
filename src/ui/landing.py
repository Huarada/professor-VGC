"""Landing-page sections: hero header, ambient background, feature cards, footer."""

from __future__ import annotations

import html
import random


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


# Idle-state landing content (hero, pills, feature cards, background), from
# the Figma design; shown only before the first analysis.

# Real formulas from the deterministic core as faint decoration, at fixed
# positions (random ones would jitter on every rerun).
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


# (left%, top%, rotation deg, font-size px), hand-placed.
_AMBIENT_POSITIONS = [
    (3, 8, -4, 11), (88, 6, 3, 11), (2, 32, 2, 10), (90, 28, -3, 10),
    (4, 58, -2, 11), (89, 55, 4, 10), (3, 82, 3, 11), (87, 80, -2, 10),
    (6, 96, -3, 10), (85, 95, 2, 10),
]


def ambient_background_html() -> str:
    """Fixed, click-through layer of faint equations behind the page (a static
    stand-in for the prototype's canvas; Streamlit won't run injected scripts).
    """
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


def hero_section_html() -> str:
    """The centered hero: icon, headline, subtitle and tag pills."""
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
  <div style="margin-bottom:18px;">{icon_html(POKEBALL_ICON, size=104)}</div>
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


# (icon builder, accent color token, title, description).
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


def feature_cards_html() -> str:
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


def grass_row_html(count: int = 20) -> str:
    """Decorative grass row, jittered with a seeded Random so reruns are stable."""
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
