"""The archetype signal tables shared by the Chaos and Smogon strategy
adapters. Abilities are scanned as well as moves: trapping often comes from
an ability (Shadow Tag), never a move.
"""

from __future__ import annotations

from src.domain.models import Archetype

_MOVE_SIGNALS: dict[str, Archetype] = {
    "trickroom": Archetype.TRICK_ROOM,
    "tailwind": Archetype.HYPER_OFFENSE,
    "perishsong": Archetype.PERISH_TRAP,
    "dragondance": Archetype.SWEEPER,
    "swordsdance": Archetype.SWEEPER,
    "nastyplot": Archetype.SWEEPER,
    "calmmind": Archetype.SWEEPER,
    "protect": Archetype.SAFE_SWAPPER,
    "fakeout": Archetype.SAFE_SWAPPER,
}

# Trapping abilities: the other half of a Perish Trap core (Mega Gengar,
# Dugtrio, Gothitelle may trap without carrying Perish Song).
_ABILITY_SIGNALS: dict[str, Archetype] = {
    "shadowtag": Archetype.PERISH_TRAP,
    "arenatrap": Archetype.PERISH_TRAP,
    "magnetpull": Archetype.PERISH_TRAP,
}


def _norm(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace("'", "")


def infer_archetypes(
    moves: list[str], abilities: list[str] | None = None
) -> list[Archetype]:
    """Archetypes from most-used moves and abilities, ordered and de-duplicated;
    BALANCE when nothing signals.
    """
    found: dict[Archetype, None] = {}
    for move in moves:
        archetype = _MOVE_SIGNALS.get(_norm(move))
        if archetype is not None:
            found.setdefault(archetype, None)
    for ability in abilities or []:
        archetype = _ABILITY_SIGNALS.get(_norm(ability))
        if archetype is not None:
            found.setdefault(archetype, None)
    return list(found.keys()) or [Archetype.BALANCE]
