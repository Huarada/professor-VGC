"""Reads a whole battle log: split lines, dispatch each command, build the state."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers import dispatch_table
from src.adapters.parsers.showdown_log.state import ParseState
from src.domain.exceptions import LogParsingError
from src.domain.models import GameState


def read_log(text: str) -> GameState:
    """Parse raw Showdown battle-log text into a :class:`GameState`.

    Raises:
        LogParsingError: The text contains no player/Pokemon lines at all.
    """
    state = ParseState()
    handlers = dispatch_table(state)
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        handler = handlers.get(parts[1])
        if handler is not None:
            handler(parts)
    if not state.roster.players:
        preview = text.strip().splitlines()[:1]
        hint = f" First line seen: {preview[0]!r}." if preview else ""
        raise LogParsingError(
            "No player or Pokemon lines were found in the battle log. A Showdown "
            "log is expected to contain '|player|p1|...', '|poke|p1|Species, L50|' "
            f"and/or '|switch|p1a: Nick|Species, L50|...' lines.{hint}"
        )
    return state.game_state()
