"""The application's game boundary: Champions rules, with Mega Evolution only."""

from __future__ import annotations

import json
import re
from typing import Any

from src.domain.exceptions import UnsupportedRulesError

_REFERENCES = re.compile(
    r"\b(?:tera|terast\w*|teracrist\w*|teracrystal\w*|tera[\s-]*(?:types?|blast|starstorm|orb)|"
    r"dynamax[a-z]*|gigantamax[a-z]*|g[\s-]?max|z[\s-]?(?:moves?|crystals?)|"
    r"golpes?\s+z|movimentos?\s+z|cristais?\s+z|primal|ultra[\s-]+(?:burst|necrozma)|"
    r"necrozma[\s-]+ultra|[a-z]+ium[\s-]?z|(?:red|blue)\s+orb|"
    r"max[\s-]*(?:airstream|darkness|flare|flutterby|geyser|guard|hailstorm|knuckle|"
    r"lightning|mindstorm|ooze|overgrowth|phantasm|quake|rockfall|starfall|steelspike|strike|wyrmwind))\b",
    re.IGNORECASE,
)
_FIELDS = {"teratype", "teratypes", "terastal", "terastallized", "dynamax", "gigantamax",
           "isdynamaxed", "isgigantamax", "zmove", "usez", "usemax", "primal",
           "candynamax", "gmax", "ultraburst", "terastallization", "zpower", "zcrystal"}
_EVENTS = {"-terastallize", "-zpower", "-primal"}
_Z_MOVES = (
    "10,000,000 Volt Thunderbolt", "Acid Downpour", "All-Out Pummeling", "Black Hole Eclipse",
    "Bloom Doom", "Breakneck Blitz", "Catastropika", "Clangorous Soulblaze", "Continental Crush",
    "Corkscrew Crash", "Devastating Drake", "Extreme Evoboost", "Genesis Supernova", "Gigavolt Havoc",
    "Guardian of Alola", "Hydro Vortex", "Inferno Overdrive", "Let's Snuggle Forever",
    "Light That Burns the Sky", "Malicious Moonsault", "Menacing Moonraze Maelstrom",
    "Never-Ending Nightmare", "Oceanic Operetta", "Pulverizing Pancake", "Savage Spin-Out",
    "Searing Sunraze Smash", "Shattered Psyche", "Sinister Arrow Raid", "Soul-Stealing 7-Star Strike",
    "Splintered Stormshards", "Stoked Sparksurfer", "Subzero Slammer", "Supersonic Skystrike",
    "Tectonic Rage", "Twinkle Tackle",
)
_Z_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(move) for move in _Z_MOVES) + r")\b", re.I)
_Z_IDS = frozenset(re.sub(r"[^a-z0-9]", "", move.lower()) for move in _Z_MOVES)


def forbidden_references(text: str) -> bool:
    """Recognize unsupported mechanics without matching species such as Porygon-Z."""
    return (_REFERENCES.search(text) is not None or _Z_PATTERN.search(text) is not None
            or re.sub(r"[^a-z0-9]", "", text.lower()) in _Z_IDS)


def require_champions_format(format_id: str | None) -> None:
    """Refuse explicit formats outside regulation-based Champions VGC."""
    if format_id and not re.fullmatch(
        r"gen9championsvgc(?:\d{4}reg[a-z]+(?:bo3)?)?", format_id.lower()
    ):
        raise UnsupportedRulesError("Only Pokemon Champions VGC regulations are supported.")


def validate_champions_input(value: Any) -> None:
    """Check structured mechanics and battle events before the parser drops fields.

    Chat/nicknames are not rules. Only protocol commands and structured set
    data are checked, so a spectator cannot invalidate an otherwise valid replay.
    """
    if isinstance(value, str):
        if value.lstrip().startswith("{"):
            try:
                validate_champions_input(json.loads(value))
            except json.JSONDecodeError:
                return  # The parser owns malformed-input errors.
        else:
            for line in value.splitlines():
                parts = line.split("|")
                command = parts[1] if len(parts) > 1 else ""
                if command == "tier" and len(parts) > 2:
                    require_champions_format(re.sub(r"[^a-z0-9]", "", parts[2].lower()))
                if command in _EVENTS or (
                    command in {"-start", "-end", "-formechange", "detailschange", "move", "-item", "-enditem", "-mega", "switch", "poke"}
                    and any(forbidden_references(p) for p in (parts[3:5] if command == "-mega" else parts[3:4]))
                ):
                    raise UnsupportedRulesError("This replay uses unsupported battle mechanics.")
        return
    if isinstance(value, dict):
        for key, entry in value.items():
            normalized = re.sub(r"[^a-z]", "", key.lower())
            if normalized in _FIELDS and entry:
                raise UnsupportedRulesError("Only Mega Evolution is supported in Champions.")
            if key in {"format", "formatid"} and isinstance(entry, str):
                require_champions_format(entry)
            if key in {"item", "moves", "battle_formes", "species"}:
                if forbidden_references(str(entry)) or re.search(r"-(?:Gmax|Primal)\b", str(entry), re.I):
                    raise UnsupportedRulesError("This set uses unsupported battle mechanics.")
            if isinstance(entry, (dict, list)) or key == "log":
                validate_champions_input(entry)
    elif isinstance(value, list):
        for entry in value:
            validate_champions_input(entry)


def clean_champions_knowledge(value: Any) -> Any:
    """Remove foreign mechanic fields and prose before they reach an agent or UI."""
    if isinstance(value, str):
        return "" if forbidden_references(value) else value
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := clean_champions_knowledge(item)) not in ("", None)]
    if isinstance(value, dict):
        return {
            key: clean_champions_knowledge(entry)
            for key, entry in value.items()
            if re.sub(r"[^a-z]", "", key.lower()) not in _FIELDS
            and not forbidden_references(key)
        }
    return value
