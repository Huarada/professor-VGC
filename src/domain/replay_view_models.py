"""UI-only models for the battle replay panel, built by the log parser's
per-turn frames (ADR-037).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReplayPokemonState(BaseModel):
    """One Pokemon's displayable state at a specific point in the replay."""

    species: str
    forme: str = ""
    """Current in-battle appearance if different from ``species`` (e.g. a
    Mega Evolution) — the panel shows this as a badge next to the sprite."""
    hp_percent: float = 100.0
    fainted: bool = False
    status: str = ""  # "par" / "brn" / "slp" / ... or "" (healthy)
    boosts: dict[str, int] = Field(default_factory=dict)
    """Net stat stage per Showdown stat abbreviation (e.g. {"atk": -1} for
    one Intimidate drop), only non-zero stages present. Reset to empty
    whenever this identity switches back in (stat stages don't persist
    across a switch, same as real game rules)."""


class ReplayTurnSnapshot(BaseModel):
    """Full visual state at the END of a turn (``turn=0`` = leads)."""

    turn: int
    active: dict[str, list[str]] = Field(default_factory=dict)
    """player -> species currently occupying each field slot, in slot order
    (e.g. ["Garchomp", "Charizard"] for p1a/p1b)."""
    pokemon: dict[str, dict[str, ReplayPokemonState]] = Field(default_factory=dict)
    """player -> species -> state, for every species that player has shown
    (active or benched) up to and including this turn. Keyed per-player
    (not a flat species dict) so a mirror match — both sides bringing the
    same species, an ordinary VGC occurrence — never collides two
    Pokemon's HP into one entry."""
    log: list[str] = Field(default_factory=list)
    """This turn's rendered event lines (moves, switches, faints), in order."""
    conditions: list[str] = Field(default_factory=list)
    """e.g. ["Tailwind p1", "Trick Room", "weather Sandstorm", "terrain
    Electric", "Gravity", "Reflect p2"] — active at some point of this turn."""


class BattleReplay(BaseModel):
    """Everything the panel needs, from a separate parse of the same input."""

    player_names: dict[str, str] = Field(default_factory=dict)  # player -> display name
    avatars: dict[str, str] = Field(default_factory=dict)  # player -> Showdown avatar id
    team: dict[str, list[str]] = Field(default_factory=dict)
    """player -> full team-preview roster (species, in preview order),
    from the "|poke|" lines — includes Pokemon never actually brought in,
    for the panel's team-icon tray (matching Showdown's own team-preview
    row)."""
    winner_player: str | None = None
    forfeited_player: str | None = None
    snapshots: list[ReplayTurnSnapshot] = Field(default_factory=list)
