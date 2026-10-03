"""Players, team preview, switches and in-battle forme changes."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import (
    STATUS_NAMES,
    parse_hp,
    split_details,
    split_ref,
)
from src.domain.models import BattleEvent


class RosterHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "player": self.on_player,
            "poke": self.on_poke,
            "switch": self.on_switch,
            "drag": self.on_switch,
            "detailschange": self.on_detailschange,
        }

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
        # The nick is the stable identity; details change after a forme change. A
        # Mega switching back in reuses its entry (and its recorded moves).
        key = nick if nick in roster.players.get(player, {}) else species
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
        timeline.events.append(
            BattleEvent(
                turn=timeline.turn, kind="switch", actor=key, actor_player=player, slot=slot,
                text=f"{player} sent out {species}.",
            )
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
