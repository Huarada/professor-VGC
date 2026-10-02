"""Per-Pokemon conditions: status, abilities, stat stages and held items."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import (
    CALC_STATS,
    ITEM_GAINED_FROM,
    STATUS_NAMES,
    tag_value,
    to_id,
)


class ConditionHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "-status": self.on_status,
            "-curestatus": self.on_curestatus,
            "-cureteam": self.on_cureteam,
            "-ability": self.on_ability,
            "-boost": self.on_boost,
            "-unboost": self.on_boost,
            "-item": self.on_item,
            "-enditem": self.on_enditem,
            "-mega": self.on_mega,
        }

    # -- status ------------------------------------------------------------- #

    def on_status(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        status = parts[3].strip()
        if key is not None and status in STATUS_NAMES:
            self.state.combatants.afflict(key, status)

    def on_curestatus(self, parts: list[str]) -> None:
        if len(parts) > 2:
            key = self.state.roster.resolve(parts[2])
            if key is not None:
                self.state.combatants.cure(key)

    def on_cureteam(self, parts: list[str]) -> None:
        if len(parts) > 2:
            self.state.combatants.cure_side(self.state.roster.side_of_ref(parts[2]))

    # -- abilities / stat stages ------------------------------------------- #

    def on_ability(self, parts: list[str]) -> None:
        # An ability that actively TRIGGERED (Intimidate, Trace, Download, ...)
        # — Showdown only announces observable activations. Recorded as a
        # timeline event AND on the roster entry so later calcs use the real,
        # CONFIRMED ability instead of a Chaos-guessed one. First reveal wins:
        # a later "-ability" for the same mon (e.g. Trace copying something)
        # is a temporary ability and must not overwrite the original.
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        ability = parts[3].strip()
        mon = self.state.roster.register(*key)
        if not mon.ability:
            mon.ability = ability
        self.state.timeline.emit(
            "ability", key, f"{key[0]} {key[1]}'s ability {ability} activated.", [ability]
        )

    def on_boost(self, parts: list[str]) -> None:
        # A real stat stage change (Intimidate, Swords Dance, a self-drop, ...)
        # — consumed by TurnReplaySimulator's running stage ledger.
        if len(parts) <= 4 or parts[3].strip() not in CALC_STATS:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        try:
            amount = int(parts[4].strip())
        except ValueError:
            return
        delta = amount if parts[1] == "-boost" else -amount
        if not delta:
            return
        self.state.roster.register(*key)
        stat = parts[3].strip()
        stages = abs(delta)
        direction = "rose" if delta > 0 else "fell"
        self.state.timeline.emit(
            "boost", key,
            f"{key[0]} {key[1]}'s {stat} {direction} by {stages} stage{'s' if stages != 1 else ''}.",
            [stat, str(delta)],
        )

    # -- items -------------------------------------------------------------- #

    def on_item(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        item = parts[3].strip()
        source = tag_value(parts[4:], "from")
        if to_id(source.split(":", 1)[-1]) in ITEM_GAINED_FROM:
            self.state.combatants.gain_item(key, item)
        else:
            self.state.combatants.reveal_item(key, item)

    def on_enditem(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is not None:
            self.state.combatants.lose_item(key, parts[3].strip())

    def on_mega(self, parts: list[str]) -> None:
        # "|-mega|p1a: Charizard|Charizard|Charizardite Y" reveals the stone.
        if len(parts) > 4 and parts[4].strip():
            key = self.state.roster.resolve(parts[2])
            if key is not None:
                self.state.combatants.reveal_item(key, parts[4].strip())
