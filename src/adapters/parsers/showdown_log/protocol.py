"""Showdown battle-protocol vocabulary and line-fragment parsing.

Pure functions and lookup tables: everything that knows Showdown's wire
format (``p1a: Nick`` references, ``45/100 par`` HP fields, ``[from] ...``
suffixes, protocol ids such as ``SunnyDay``) lives here, so no protocol
string ever leaks past this package.
"""

from __future__ import annotations

import re

_REF = re.compile(r"^(?P<player>p\d)(?P<slot>[a-z]?): (?P<nick>.+)$")

# Protect-family names as they appear in "-activate|POKEMON|move: X" when that
# move fully blocked the current attack (protocol vocabulary, not move data).
PROTECT_FAMILY = {
    "protect", "detect", "spikyshield", "banefulbunker", "kingsshield",
    "quickguard", "wideguard", "craftyshield", "obstruct", "silktrap",
    "burningbulwark", "matblock", "maxguard",
}

# The five stat stages that actually feed the damage/speed calc — accuracy
# and evasion changes are real Showdown -boost/-unboost events too, but
# neither @smogon/calc's boosts option nor this project's field model uses
# them, so they're deliberately not tracked.
CALC_STATS = {"atk", "def", "spa", "spd", "spe"}

# Showdown weather/terrain/side-condition ids -> in-game (domain) names.
WEATHER = {
    "sunnyday": "Sun", "raindance": "Rain", "sandstorm": "Sand", "hail": "Hail",
    "snow": "Snow", "snowscape": "Snow", "desolateland": "Harsh Sunshine",
    "primordialsea": "Heavy Rain", "deltastream": "Strong Winds",
}
TERRAIN = {
    "electricterrain": "Electric", "grassyterrain": "Grassy",
    "psychicterrain": "Psychic", "mistyterrain": "Misty",
}
SCREENS = {"reflect": "Reflect", "lightscreen": "Light Screen", "auroraveil": "Aurora Veil"}
ROOMS = {"gravity": "Gravity", "magicroom": "Magic Room", "wonderroom": "Wonder Room"}
STATUS_NAMES = {
    "par": "paralysis", "brn": "burn", "psn": "poison", "tox": "bad poison",
    "slp": "sleep", "frz": "freeze",
}
# "[from] move: X" / "[from] ability: X" sources that GIVE the subject a new item.
ITEM_GAINED_FROM = {
    "trick", "switcheroo", "bestow", "thief", "covet", "recycle",
    "harvest", "pickup", "magician", "pickpocket",
}


def to_id(text: str) -> str:
    """Showdown-style id: lowercase alphanumerics only (``"Sunny Day"`` -> ``"sunnyday"``)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tag_value(parts: list[str], tag: str) -> str:
    """Value of a ``[tag] value`` suffix part (``""`` when absent)."""
    prefix = f"[{tag}]"
    for part in parts:
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix):].strip()
    return ""


def split_ref(ref: str) -> tuple[str, str, str] | None:
    """Split ``"p1a: Torkoal"`` into ``("p1", "a", "Torkoal")``."""
    m = _REF.match(ref.strip())
    if not m:
        return None
    return m.group("player"), m.group("slot"), m.group("nick").strip()


def parse_hp(text: str) -> tuple[float | None, str]:
    """``"45/100 par"`` -> ``(45.0, "par")``; ``"0 fnt"`` -> ``(0.0, "fnt")``;
    ``"50/100g"`` -> ``(50.0, "")`` (Showdown's HP-bar colour suffix)."""
    pieces = text.strip().split()
    if not pieces:
        return None, ""
    condition = pieces[1] if len(pieces) > 1 else ""
    if "/" not in pieces[0]:
        try:
            return float(pieces[0]), condition
        except ValueError:
            return None, condition
    current, _, maximum = pieces[0].partition("/")
    maximum = maximum.rstrip("gyr")  # HP-bar colour: green / yellow / red
    try:
        cur, top = float(current), float(maximum)
    except ValueError:
        return None, condition
    if top <= 0:
        return None, condition
    return round(cur / top * 100, 1), condition


def split_details(details: str) -> tuple[str, int]:
    """Split ``"Garchomp, L50, M"`` into ``("Garchomp", 50)``."""
    parts = [p.strip() for p in details.split(",")]
    level = 50
    for part in parts[1:]:
        if part.upper().startswith("L") and part[1:].isdigit():
            level = int(part[1:])
    return parts[0], level
