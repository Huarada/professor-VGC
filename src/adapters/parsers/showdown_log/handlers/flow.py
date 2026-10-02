"""Battle flow: turns, faints, forfeits and the winner."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import split_ref


class FlowHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "turn": self.on_turn,
            "faint": self.on_faint,
            "-message": self.on_message,
            "win": self.on_win,
        }

    def on_turn(self, parts: list[str]) -> None:
        timeline = self.state.timeline
        try:
            timeline.turn = int(parts[2])
        except (IndexError, ValueError):
            pass
        timeline.current_move = None

    def on_faint(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        split = split_ref(parts[2])
        key = self.state.roster.resolve(parts[2])
        if split is None or key is None:
            return
        self.state.combatants.faint(key[0] + split[1], key)
        self.state.timeline.record_faint(key)

    def on_message(self, parts: list[str]) -> None:
        # e.g. "|-message|42 s forfeited." — the game ended WITHOUT the
        # forfeiting side's team being defeated in play.
        if len(parts) > 2 and parts[2].strip().endswith("forfeited."):
            self.state.timeline.forfeited_name = parts[2].strip()[: -len(" forfeited.")].strip()

    def on_win(self, parts: list[str]) -> None:
        if len(parts) > 2:
            self.state.timeline.winner_name = parts[2].strip()
