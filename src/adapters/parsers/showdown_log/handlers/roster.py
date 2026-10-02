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

    def on_poke(self, parts: list[str]) -> None:
        if len(parts) > 3:
            species, level = split_details(parts[3])
            self.state.roster.register(parts[2], species, level)

    def on_switch(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        split = split_ref(parts[2])
        if split is None:
            return
        player, slot, nick = split
        species, level = split_details(parts[3])
        roster, combatants, timeline = self.state.roster, self.state.combatants, self.state.timeline
        # Showdown's "nick" (the ref before the colon) is the STABLE identity
        # for this battle slot; "details" is just the CURRENT appearance and
        # changes after a Mega Evolution / in-battle forme change. If this
        # Pokemon was already registered under its nick (e.g. it mega evolved,
        # switched out, and is now switching back in as "<Species>-Mega"),
        # reuse that same entry instead of registering a fresh one — otherwise
        # the new entry starts with an empty moveset and later gets treated as
        # a different Pokemon than the one whose moves were already recorded.
        key = nick if nick in roster.players.get(player, {}) else species
        roster.register(player, key, level)
        roster.record_forme(player, key, species)
        roster.mark_brought(player, key)
        roster.slot_species[player + slot] = key
        combatants.enter(player + slot, (player, key))
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
        # Mega Evolution / other in-battle forme change (e.g. Zygarde,
        # Mimikyu-Busted). The identity doesn't change — only the appearance —
        # but it is recorded so calcs can use the real forme's stats, and it is
        # a visible timeline event: it can be the single most strategically
        # important fact in the game (e.g. Mega Gengar gaining Shadow Tag).
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        species, _level = split_details(parts[3])
        self.state.roster.record_forme(key[0], key[1], species)
        self.state.timeline.emit(
            "forme_change", key, f"{key[0]} {key[1]} transformed into {species}.", [species]
        )
