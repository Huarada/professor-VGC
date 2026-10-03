"""Each Pokemon's running battle state: HP, status, held item, field presence.

Status and item changes are also timeline facts (the explanation narrates a
burn or a consumed berry), so this ledger records the state AND emits the
matching ordered event.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_log.protocol import STATUS_NAMES
from src.adapters.parsers.showdown_log.roster import Key, Roster
from src.adapters.parsers.showdown_log.timeline import Timeline


class CombatantLedger:
    """HP / status / items / who is on the field, per ``(player, key)``."""

    def __init__(self, roster: Roster, timeline: Timeline) -> None:
        self._roster = roster
        self._timeline = timeline
        self.hp: dict[Key, float] = {}
        self.status: dict[Key, str] = {}
        self.held: dict[Key, str] = {}  # current item when determined ("" = none)
        self.gained_item: set[Key] = set()
        self.revealed: set[tuple[str, str, str]] = set()
        self.on_field: dict[str, Key] = {}  # "p1a" -> key
        self.fainted: dict[str, list[str]] = {}
        self.boosts: dict[Key, dict[str, int]] = {}
        self.forme: dict[Key, str] = {}  # current appearance when not the key itself

    # -- field presence ----------------------------------------------------- #

    def enter(self, slot_ref: str, key: Key, appearance: str) -> None:
        self.on_field[slot_ref] = key
        self.boosts[key] = {}  # stat stages never survive a switch
        self.set_forme(key, appearance)

    def set_forme(self, key: Key, appearance: str) -> None:
        if appearance == key[1]:
            self.forme.pop(key, None)
        else:
            self.forme[key] = appearance

    def boost(self, key: Key, stat: str, delta: int) -> None:
        stages = self.boosts.setdefault(key, {})
        stage = max(-6, min(6, stages.get(stat, 0) + delta))
        if stage:
            stages[stat] = stage
        else:
            stages.pop(stat, None)

    def faint(self, slot_ref: str, key: Key) -> None:
        self.hp[key] = 0.0
        self.on_field.pop(slot_ref, None)
        self.fainted.setdefault(key[0], []).append(key[1])

    def active(self) -> dict[str, list[str]]:
        """player -> species on the field, in slot order."""
        result: dict[str, list[str]] = {}
        for slot_ref in sorted(self.on_field):
            player, key = self.on_field[slot_ref]
            result.setdefault(player, []).append(key)
        return result

    # -- status ------------------------------------------------------------- #

    def afflict(self, key: Key, status: str) -> None:
        self.status[key] = status
        self._timeline.emit(
            "status", key, f"{key[0]} {key[1]} was afflicted with {STATUS_NAMES[status]}.",
            [status],
        )

    def cure(self, key: Key) -> None:
        status = self.status.pop(key, "")
        if status:
            self._timeline.emit(
                "cure", key, f"{key[0]} {key[1]} was cured of {STATUS_NAMES[status]}.", [status]
            )

    def cure_side(self, player: str) -> None:
        for key in [k for k in self.status if k[0] == player]:
            self.cure(key)

    # -- items -------------------------------------------------------------- #

    def reveal_item(self, key: Key, item: str) -> None:
        """``key`` is seen holding ``item`` (its original item unless it gained one)."""
        mon = self._roster.register(*key)
        if mon.item is None and key not in self.gained_item:
            mon.item = item
        self.held[key] = item
        marker = (key[0], key[1], item)
        if marker not in self.revealed:
            self.revealed.add(marker)
            self._timeline.emit(
                "item", key, f"{key[0]} {key[1]}'s item revealed: {item}.", [item, "revealed"]
            )

    def gain_item(self, key: Key, item: str) -> None:
        """``key`` obtained ``item`` (Trick, Switcheroo, Harvest, ...)."""
        self.gained_item.add(key)
        self.held[key] = item
        self._timeline.emit("item", key, f"{key[0]} {key[1]} obtained {item}.", [item, "acquired"])

    def lose_item(self, key: Key, item: str) -> None:
        """``key``'s ``item`` was consumed or removed."""
        mon = self._roster.register(*key)
        if mon.item is None and key not in self.gained_item:
            mon.item = item  # it was the original item all along
        self.held[key] = ""
        self._timeline.emit("item", key, f"{key[0]} {key[1]} lost its {item}.", [item, "lost"])

    # -- result ------------------------------------------------------------ #

    def final_statuses(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for (player, key), status in self.status.items():
            result.setdefault(player, {})[key] = status
        return result
