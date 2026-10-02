"""Protocol-command handlers, one group per concern.

Each group maps Showdown protocol commands (``move``, ``-damage``,
``-weather``, ...) to small methods that update the shared
:class:`~src.adapters.parsers.showdown_log.state.ParseState`. The reader
merges every group's routes into one dispatch table.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.actions import ActionHandlers
from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.handlers.conditions import ConditionHandlers
from src.adapters.parsers.showdown_log.handlers.field import FieldHandlers
from src.adapters.parsers.showdown_log.handlers.flow import FlowHandlers
from src.adapters.parsers.showdown_log.handlers.roster import RosterHandlers
from src.adapters.parsers.showdown_log.state import ParseState

HANDLER_GROUPS: tuple[type[HandlerGroup], ...] = (
    FlowHandlers, RosterHandlers, ActionHandlers, FieldHandlers, ConditionHandlers,
)


def dispatch_table(state: ParseState) -> dict[str, Handler]:
    """Every protocol command this reader understands -> its handler."""
    table: dict[str, Handler] = {}
    for group in HANDLER_GROUPS:
        routes = group(state).routes()
        overlap = table.keys() & routes.keys()
        if overlap:
            raise RuntimeError(f"Protocol commands handled twice: {sorted(overlap)}")
        table.update(routes)
    return table


__all__ = ["Handler", "HandlerGroup", "dispatch_table"]
