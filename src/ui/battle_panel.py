"""Showdown-like battle replay panel.

Deliberately independent of the LLM pipeline: it consumes a BattleReplay
from the standalone replay-viewer parser (obtained through the Container),
never the AnalysisResult DTO. A bug here cannot affect the LLM answer, and
vice versa.
"""

from __future__ import annotations

import html
import json
import re
from typing import cast
from urllib.parse import quote

import requests
import streamlit as st

from src.domain.replay_view_models import BattleReplay, ReplayPokemonState, ReplayTurnSnapshot
from src.ui.theme import LAB_BACKGROUND_CSS_DEFAULT, background_css


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
    # In-game names used by the analysis pipeline's own condition labels
    # ("weather Sun", "terrain Electric"); the Showdown ids above come from
    # the replay viewer's labels.
    "sun": "weather-sunnyday.jpg", "harshsunshine": "weather-sunnyday.jpg",
    "rain": "weather-raindance.jpg", "heavyrain": "weather-raindance.jpg",
    "sand": "weather-sandstorm.png", "strongwinds": "weather-strongwind.png",
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
PLACEHOLDER_SPRITE = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<circle cx="32" cy="32" r="29" fill="#eee" stroke="#333" stroke-width="3"/>'
    '<path d="M3 32h58" stroke="#333" stroke-width="3"/>'
    '<circle cx="32" cy="32" r="9" fill="#eee" stroke="#333" stroke-width="3"/>'
    "</svg>"
)


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
def http_head_ok(url: str, timeout: float = 2.0) -> bool:
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
    `http_head_ok` above, decorated with `st.cache_data`, Streamlit's own
    primitive for state that must survive reruns (and, as a bonus, survives
    across sessions on the same server process too, so this cost is now
    paid once per URL ever, not once per URL per rerun)."""
    try:
        return http_head_ok(url, timeout)
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
        seen = [PLACEHOLDER_SPRITE]
    if seen[-1] != PLACEHOLDER_SPRITE:
        seen.append(PLACEHOLDER_SPRITE)
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
    elif condition.startswith("terrain "):
        name = condition[len("terrain "):].lower().replace(" ", "") + "terrain"
    else:
        return None
    fname = _WEATHER_ICON_FILES.get(name)
    return f"https://play.pokemonshowdown.com/fx/{fname}" if fname else None


def _condition_badge_html(condition: str) -> str:
    icon_url = _condition_icon_url(condition)
    icon_img = (
        f'<img src="{icon_url}" alt="" style="width:15px;height:15px;border-radius:3px;'
        f"vertical-align:middle;margin-right:4px;object-fit:cover;\" "
        f"onerror=\"this.style.display='none';\" />"
        if icon_url
        else ""
    )
    return (
        f'<span style="display:inline-flex;align-items:center;background:#00000012;'
        f"border:1px solid #00000022;border-radius:4px;padding:1px 7px;margin:0 4px 4px 0;"
        f'font-size:11px;color:inherit;">{icon_img}'
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


def hp_box_html(state: ReplayPokemonState) -> str:
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
    (NOT absolutely positioned — see battle_stage_html for why: a corner
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
  {hp_box_html(state)}
  {sprite_img}
</div>
"""


def battle_stage_html(replay: BattleReplay, snapshot: ReplayTurnSnapshot) -> str:
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
    stage_background = background_css("battle-stage", LAB_BACKGROUND_CSS_DEFAULT)

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


def current_turn_number(replay: BattleReplay) -> int:
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


def highlight_answer_by_turn(answer_md: str, turn: int) -> str:
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


def render_battle_panel(replay: BattleReplay) -> None:
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
    st.markdown(battle_stage_html(replay, snapshot), unsafe_allow_html=True)

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
