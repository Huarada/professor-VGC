"""Domain models for the Showdown-like battle replay panel (UI-only).

Deliberately independent of ``src/domain/models.py`` (GameState/BattleOutcome/
AnalysisResult) — the LLM analysis pipeline's models. This file exists so the
UI's turn-by-turn visualization can never regress, depend on, or be regressed
by the LLM pipeline's own parsing/domain logic. See
``src/adapters/parsers/replay_viewer_parser.py`` for the (also independent)
parser that produces these, and ADR-014 in ADR.md for why this isolation was
a deliberate, hard requirement rather than an extension of the existing
parser/models.
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
    """Full visual battle state at the END of one turn.

    ``turn=0`` is "leads" — the state after both sides send out their
    initial Pokemon, before turn 1's actions resolve. Everything the
    Showdown-like panel needs to render this point in the replay lives here;
    the UI never has to cross-reference another module's data for a given
    turn.
    """

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
    """e.g. ["Tailwind p1", "Trick Room", "weather Sandstorm"] — active this
    turn."""


class BattleReplay(BaseModel):
    """Everything the Showdown-like panel needs, self-contained.

    Produced by ``parse_replay_for_viewer`` from the same raw replay input
    the user pastes for the LLM analysis, but via a completely separate
    parsing pass — see module docstring.
    """

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
