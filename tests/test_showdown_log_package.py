"""Structure guard for the Showdown log reader package.

The reader is split into handler groups merged into one dispatch table
(ADR-032). These tests pin the exact protocol surface it understands, so a
handler can't silently disappear (or be registered twice) in a refactor.
"""

from __future__ import annotations

import pytest

from src.adapters.parsers.showdown_log.handlers import HANDLER_GROUPS, dispatch_table
from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.state import ParseState

_EXPECTED_COMMANDS = {
    # flow
    "turn", "tier", "faint", "-message", "win",
    # roster
    "player", "poke", "switch", "drag", "detailschange",
    # actions
    "move", "-damage", "-heal", "-sethp", "-activate", "-singleturn",
    "-supereffective", "-resisted", "-immune", "-crit", "-miss",
    # field
    "-sidestart", "-sideend", "-fieldstart", "-fieldend", "-weather",
    # conditions
    "-status", "-curestatus", "-cureteam", "-ability", "-boost", "-unboost",
    "-item", "-enditem", "-mega",
}


def test_dispatch_table_covers_exactly_the_supported_protocol():
    assert set(dispatch_table(ParseState())) == _EXPECTED_COMMANDS


def test_a_command_handled_by_two_groups_is_rejected(monkeypatch):
    class _Duplicate(HandlerGroup):
        def routes(self) -> dict[str, Handler]:
            return {"move": lambda parts: None}

    monkeypatch.setattr(
        "src.adapters.parsers.showdown_log.handlers.HANDLER_GROUPS",
        (*HANDLER_GROUPS, _Duplicate),
    )
    with pytest.raises(RuntimeError, match="move"):
        dispatch_table(ParseState())
