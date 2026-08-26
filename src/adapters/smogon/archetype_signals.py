"""Shared archetype-inference signal tables — one copy, not two.

Both strategy providers (ChaosStrategyAdapter, sourced from local Chaos
dumps, and SmogonDexAdapter, sourced from the official @pkmn/smogon data)
need to turn "this species' most-used moves/ability" into a coarse
Archetype tag. They used to each carry their own near-identical
_ARCHETYPE_SIGNALS dict, which had already drifted apart — one had a
"shadowtag" entry the other didn't, and neither actually worked (see
below) — a duplication smell the same way ADR-013 flagged for spread
parsing: one correct implementation, not two that can silently diverge.

Reported gap: a Perish-Trap-style core built around a Pokemon whose
TRAPPING comes from its ABILITY (Shadow Tag, Arena Trap, Magnet Pull —
e.g. Mega Gengar, which gains Shadow Tag only upon Mega Evolving) was
never detected, because both adapters' signal tables only ever scanned
MOVE names — including a "shadowtag" entry that could never match
anything, since Shadow Tag is never a move. Fixed by scanning abilities
too, not just moves.
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

# Trapping abilities are the OTHER half of a "Perish Trap" core (pin the
# target in place, usually with Perish Song or residual damage doing the
# actual work) — several real VGC trappers (Mega Gengar, Dugtrio,
# Gothitelle) trap via ability alone and may never carry Perish Song
# themselves if a teammate provides the finishing move instead.
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
    """Coarse archetype tags from a species' most-used moves and (optionally)
    abilities. Order-preserving, de-duplicated; falls back to BALANCE when
    nothing signals — never an empty list, matching every existing caller's
    expectation."""
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
