"""Moves and their direct results: damage, healing, blocks, effectiveness."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import (
    PROTECT_FAMILY,
    parse_hp,
    split_ref,
    tag_value,
    to_id,
)
from src.domain.models import BattleEvent


class ActionHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "move": self.on_move,
            "-damage": self.on_damage,
            "-heal": self.on_heal,
            "-sethp": self.on_heal,
            "-activate": self.on_activate,
            "-singleturn": self.on_singleturn,
            "-supereffective": lambda parts: self.state.timeline.add_effect("super effective"),
            "-resisted": lambda parts: self.state.timeline.add_effect("resisted"),
            "-immune": lambda parts: self.state.timeline.add_effect("immune"),
            "-crit": lambda parts: self.state.timeline.add_effect("crit"),
            "-miss": lambda parts: self.state.timeline.add_effect("missed"),
        }

    def on_move(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        timeline, roster = self.state.timeline, self.state.roster
        split = split_ref(parts[2])
        if split is None:
            timeline.current_move = None
            return
        player, slot, nick = split
        move = parts[3].strip()
        bucket = roster.players.get(player, {})
        brought = roster.brought.get(player, [])
        species = (
            nick if nick in bucket
            else roster.slot_species.get(player + slot) or (brought[-1] if brought else None)
        )
        mon = bucket.get(species) if species else None
        if mon is not None and move and move not in mon.moves:
            mon.moves.append(move)
        actor = species or nick
        effects: list[str] = []
        if any("[spread]" in p for p in parts[4:]):
            effects.append("spread")
        if (timeline.turn, player, actor) in timeline.helped:
            effects.append("helping hand")
        timeline.current_move = BattleEvent(
            turn=timeline.turn, kind="move", actor=actor, actor_player=player, slot=slot,
            move=move, effects=effects, state=self.state.snapshot(),
        )
        timeline.events.append(timeline.current_move)

    def on_damage(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        roster, combatants, timeline = self.state.roster, self.state.combatants, self.state.timeline
        key = roster.resolve(parts[2])
        if key is None:
            return
        hp, condition = parse_hp(parts[3])
        fainted = condition == "fnt"
        if hp is not None:
            combatants.hp[key] = 0.0 if fainted else hp
        source = tag_value(parts[4:], "from")
        if source:
            if source.lower().startswith("item:"):
                holder = roster.resolve(tag_value(parts[4:], "of")) or key
                combatants.reveal_item(holder, source.split(":", 1)[1].strip())
            return  # residual / item / ability damage, not the move's direct hit
        move = timeline.current_move
        if move is None:
            return
        _player, species = key
        if species not in move.targets:
            move.targets.append(species)
        if fainted:
            move.results.append(f"{species} fainted")
            timeline.fainted_in_move.add((timeline.turn, species))
            timeline.record_hit(key, 0.0)
        else:
            pct = parts[3].strip().split("/")[0]
            move.results.append(f"{species}->{pct}%")
            timeline.record_hit(key, hp)

    def on_heal(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        hp, _condition = parse_hp(parts[3])
        if hp is not None:
            self.state.combatants.hp[key] = hp
        source = tag_value(parts[4:], "from")
        if source.lower().startswith("item:"):
            self.state.combatants.reveal_item(key, source.split(":", 1)[1].strip())

    def on_activate(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        reason = parts[3].strip()
        key = self.state.roster.resolve(parts[2])
        if key is None:
            return
        if reason.lower().startswith("item:"):
            self.state.combatants.reveal_item(key, reason.split(":", 1)[1].strip())
            return
        # A Protect-family block: the blocker gets no -damage line, so record that
        # the move was aimed at it.
        timeline = self.state.timeline
        move = timeline.current_move
        if move is None or not reason.lower().startswith("move:"):
            return
        block_move = reason.split(":", 1)[1].strip()
        if to_id(block_move) not in PROTECT_FAMILY:
            return
        _player, species = key
        if species not in move.blocked:
            move.blocked.append(species)
        move.results.append(f"{species} blocked ({block_move})")
        timeline.record_hit(key, self.state.combatants.hp.get(key), blocked_by=block_move)

    def on_singleturn(self, parts: list[str]) -> None:
        # "|-singleturn|p1b: Garchomp|move: Helping Hand|[of] p1a: Amoonguss":
        # the TARGET's move later this turn is boosted 1.5x.
        if len(parts) > 3 and to_id(parts[3].replace("move:", "")) == "helpinghand":
            key = self.state.roster.resolve(parts[2])
            if key is not None:
                self.state.timeline.helped.add((self.state.turn, key[0], key[1]))
