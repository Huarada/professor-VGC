"""Common shape of a handler group."""

from __future__ import annotations

from typing import Callable

from src.adapters.parsers.showdown_log.state import ParseState

Handler = Callable[[list[str]], None]
"""Receives the ``|``-split protocol line (``parts[1]`` is the command)."""


class HandlerGroup:
    """A cohesive set of protocol-command handlers over one shared state."""

    def __init__(self, state: ParseState) -> None:
        self.state = state

    def routes(self) -> dict[str, Handler]:
        """Protocol command -> handler, for the reader's dispatch table."""
        raise NotImplementedError
