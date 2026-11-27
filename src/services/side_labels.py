"""Name the side of every Pokemon in the BO1 explanation context (ADR-082).

The log says "p1 Kingambit"; the question says "Player A". A model that has to map one onto the other in a mirror-ish
matchup (both sides bring Kingambit, Basculegion, Sneasler...) credits actions to the wrong side. Here every side marker
is rewritten to the player's own name ("Sauron's Kingambit"), every `*_player` field carries the name, and a hit's
`Target->47%` becomes "Sauron's Target left at 47% HP", so a remaining-HP figure is never read as damage.
"""

from __future__ import annotations

import re
from typing import Any

from src.domain.models import BattleEvent, GameState

_BEFORE_SPECIES = re.compile(r"\bp([12])(?=\s+[A-Z])")
_POSSESSIVE = re.compile(r"\bp([12])(?=['’]s\b)")
_PLAIN = re.compile(r"\bp([12])\b")
_RESULT = re.compile(r"^(?P<mon>.+?)(?:->(?P<pct>\d+(?:\.\d+)?)%| fainted| blocked \()")


def side_names(game_state: GameState) -> dict[str, str]:
    return {side.player: side.player_name or side.player for side in game_state.sides}


def label_text(text: str, names: dict[str, str]) -> str:
    """"p1 Kingambit used X" -> "Sauron's Kingambit used X"; "Tailwind p2" -> "Tailwind Ana"."""
    def owner(match: re.Match[str]) -> str:
        return names.get(f"p{match.group(1)}", match.group(0))

    text = _BEFORE_SPECIES.sub(lambda m: owner(m) + "'s", text)
    text = _POSSESSIVE.sub(owner, text)
    return _PLAIN.sub(owner, text)


def label_sides(value: Any, names: dict[str, str]) -> Any:
    """The same value with every side marker (`p1`/`p2` text, `*player` fields) replaced by the player's name."""
    if isinstance(value, str):
        return label_text(value, names)
    if isinstance(value, list):
        return [label_sides(item, names) for item in value]
    if isinstance(value, dict):
        return {key: names.get(item, item) if key.endswith("player") and isinstance(item, str)
                else label_sides(item, names) for key, item in value.items()}
    return value


def event_line(event: BattleEvent, names: dict[str, str]) -> str:
    """One timeline line with the owner of every actor and target, remaining HP spelled out, the user's own partner
    marked, and an immune or missed target named (a spread move's "(immune)" alone reads as an opponent's)."""
    if event.kind != "move":
        return label_text(event.text, names)

    def who(player: str, species: str) -> str:
        partner = " [its own partner]" if player == event.actor_player and species != event.actor else ""
        return f"{names.get(player, player)}'s {species}{partner}"

    effects = list(event.effects)
    unaffected = [entry.partition(":") for entry in event.unaffected_targets]
    opposing = [(player, species) for player, _, species in unaffected if player != event.actor_player]
    reasons = [effect for effect in effects if effect in ("immune", "missed")]
    if opposing and len(reasons) == 1:
        effects[effects.index(reasons[0])] = f"{reasons[0]}: {', '.join(who(*target) for target in opposing)}"
    partner = [(player, species) for player, _, species in unaffected if player == event.actor_player]
    partner_reasons = [effect for effect in event.partner_effects if effect in ("immune", "missed")]
    if partner and len(partner_reasons) == 1:
        effects.append(f"{', '.join(who(*target) for target in partner)} {partner_reasons[0]}")
    line = label_text(f"{event.actor_player} {event.actor} used {event.move}", names)
    if effects:
        line += f" ({', '.join(effects)})"
    _head, separator, tail = event.text.partition(" — ")
    if not separator:
        return line
    owners: dict[str, set[str]] = {}
    for hit in event.hits:
        owners.setdefault(hit.species, set()).add(hit.player)
    parts = []
    for part in tail.split("; "):
        found = _RESULT.match(part)
        if found is None:
            parts.append(part)
            continue
        mon, remaining = found.group("mon"), found.group("pct")
        players = owners.get(mon, set())
        subject = who(next(iter(players)), mon) if len(players) == 1 and next(iter(players)) in names else mon
        parts.append(f"{subject} left at {remaining}% HP" if remaining else subject + part[len(mon):])
    return f"{line} — " + "; ".join(parts)
