"""Players, team preview, switches and in-battle forme changes."""

from __future__ import annotations

from typing import TypeVar

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import (
    STATUS_NAMES,
    parse_hp,
    split_details,
    split_ref,
    tag_value,
)
from src.domain.models import BattleEvent

_T = TypeVar("_T")


def _swap_slots(mapping: dict[str, _T], first: str, second: str) -> None:
    """Exchange what two battle positions hold (either may be empty)."""
    held = [mapping.pop(first, None), mapping.pop(second, None)]
    for slot, value in zip((second, first), held):
        if value is not None:
            mapping[slot] = value


class RosterHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "player": self.on_player,
            "poke": self.on_poke,
            "switch": self.on_switch,
            "drag": self.on_switch,
            "detailschange": self.on_detailschange,
            "-transform": self.on_transform,
            "swap": self.on_swap,
        }

    def on_swap(self, parts: list[str]) -> None:
        # Ally Switch: ``|swap|p2b: Farigiraf|0`` moves it to position 0 (slot a)
        # and its ally to the slot it left. Every later reference uses the new slots.
        split = split_ref(parts[2]) if len(parts) > 3 else None
        if split is None or not parts[3].strip().isdigit():
            return
        player, slot, _nick = split
        target = player + "ab"[int(parts[3]) % 2]
        origin = player + slot
        if origin == target:
            return
        _swap_slots(self.state.roster.slot_species, origin, target)
        _swap_slots(self.state.combatants.on_field, origin, target)

    def on_player(self, parts: list[str]) -> None:
        roster = self.state.roster
        if len(parts) > 2:
            roster.players.setdefault(parts[2], {})
            if len(parts) > 3 and parts[3].strip():
                roster.player_names[parts[2]] = parts[3].strip()
            if len(parts) > 4 and parts[4].strip():
                roster.avatars[parts[2]] = parts[4].strip()

    def on_poke(self, parts: list[str]) -> None:
        if len(parts) > 3:
            species, level = split_details(parts[3])
            self.state.roster.register(parts[2], species, level)
            self.state.roster.preview.setdefault(parts[2], []).append(species)

    def on_switch(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        split = split_ref(parts[2])
        if split is None:
            return
        player, slot, nick = split
        species, level = split_details(parts[3])
        roster, combatants, timeline = self.state.roster, self.state.combatants, self.state.timeline
        # The nickname is the stable identity; details change after a forme
        # change. A Mega switching back in ("Dragonite-Mega" under a nickname)
        # reuses its entry, never a new one.
        key = roster.nicks.get((player, nick)) or (
            nick if nick in roster.players.get(player, {}) else species
        )
        roster.nicks[(player, nick)] = key
        roster.register(player, key, level)
        roster.record_forme(player, key, species)
        roster.mark_brought(player, key)
        roster.slot_species[player + slot] = key
        combatants.enter(player + slot, (player, key), species)
        if len(parts) > 4:
            hp, condition = parse_hp(parts[4])
            if hp is not None:
                combatants.hp[(player, key)] = hp
            if condition in STATUS_NAMES:
                combatants.status[(player, key)] = condition
        timeline.current_move = None
        disguise = tag_value(parts[5:], "illusion")  # set by unmask_illusions
        shown = f" (disguised as {disguise} by Illusion)" if disguise else ""
        timeline.events.append(
            BattleEvent(
                turn=timeline.turn, kind="switch", actor=key, actor_player=player, slot=slot,
                text=f"{player} sent out {species}{shown}.",
            )
        )

    def on_transform(self, parts: list[str]) -> None:
        # Transform / Imposter: the user takes the target's species (stats,
        # types, ability) until it switches out; a new switch-in resets it.
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        target = self.state.roster.resolve(parts[3])
        if key is None or target is None:
            return
        appearance = self.state.combatants.forme.get(target, target[1])
        self.state.roster.record_forme(key[0], key[1], appearance)
        self.state.combatants.set_forme(key, appearance)
        self.state.timeline.emit(
            "forme_change", key,
            f"{key[0]} {key[1]} transformed into {target[0]} {appearance}.", [appearance],
        )

    def on_detailschange(self, parts: list[str]) -> None:
        # Forme change (Mega, Mimikyu-Busted): same identity, new stats, and a
        # visible timeline event (e.g. Mega Gengar gaining Shadow Tag).
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        species, _level = split_details(parts[3])
        self.state.roster.record_forme(key[0], key[1], species)
        self.state.combatants.set_forme(key, species)
        self.state.timeline.emit(
            "forme_change", key, f"{key[0]} {key[1]} transformed into {species}.", [species]
        )
