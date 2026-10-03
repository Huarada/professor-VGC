"""Reads a whole battle log: split lines, dispatch each command, build the state."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers import dispatch_table
from src.adapters.parsers.showdown_log.replay_frames import build_battle_replay
from src.adapters.parsers.showdown_log.state import ParseState
from src.domain.exceptions import LogParsingError
from src.domain.models import GameState
from src.domain.replay_view_models import BattleReplay


def _read(text: str) -> ParseState:
    """Dispatch every protocol line of ``text`` into a fresh state.

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
    return state


def read_log(text: str) -> GameState:
    """Parse raw Showdown battle-log text into a :class:`GameState`.

    Raises:
        LogParsingError: The text contains no player/Pokemon lines at all.
    """
    return _read(text).game_state()


def read_replay(text: str) -> BattleReplay:
    """The battle panel's turn-by-turn view of the same log; empty when the
    text is not a battle log."""
    try:
        state = _read(text)
    except LogParsingError:
        return BattleReplay()
    state.capture_frame()  # the last turn has no trailing |turn| line
    game = state.game_state()
    return build_battle_replay(state.frames, state.roster, state.field.room_windows, game)
