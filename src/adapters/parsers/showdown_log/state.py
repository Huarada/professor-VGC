"""The parse-time aggregate shared by every protocol handler."""

from __future__ import annotations

from typing import Literal

from src.adapters.parsers.showdown_log.combatants import CombatantLedger
from src.adapters.parsers.showdown_log.field_ledger import FieldLedger
from src.adapters.parsers.showdown_log.protocol import PROTECT_FAMILY, split_ref, tag_value, to_id
from src.adapters.parsers.showdown_log.replay_frames import TurnFrame
from src.adapters.parsers.showdown_log.roster import Roster
from src.adapters.parsers.showdown_log.timeline import Timeline
from src.domain.models import AllyEffect, BattleSnapshot, GameState, MonState


# Their `-heal`/`-immune`/`-boost` lines carry the attacker (the effect source) in [of].
_ABSORBING_ABILITIES = frozenset({
    "waterabsorb", "voltabsorb", "dryskin", "sapsipper", "stormdrain", "lightningrod", "motordrive",
    "eartheater", "wellbakedbody", "windrider", "flashfire"})
# After any of these, later lines are no longer results of the last move.
_MOVE_BLOCK_ENDS = frozenset({"", "switch", "drag", "upkeep", "turn", "cant", "detailschange", "-mega"})
# Pickpocket/Magician: [of] is the victim, the ability belongs to the thief.
_THIEF_ABILITIES = frozenset({"pickpocket", "magician"})


def _ally_effect(command: str, parts: list[str]) -> tuple[Literal["immune", "protected", "ability", "boosted", "healed"], str] | None:
    """(effect, detail) when a line about the partner is an interaction rather than plain damage or an item tick."""
    value = parts[3].strip() if len(parts) > 3 else ""
    source = tag_value(parts[3:], "from")
    ability = source.split(":", 1)[1].strip() if source.lower().startswith("ability:") else ""
    if command == "-immune":
        return "immune", ability
    if command == "-ability":
        return "ability", value
    if command in {"-activate", "-start"} and value.lower().startswith("ability:"):
        return "ability", value.split(":", 1)[1].strip()
    if command == "-activate" and value.lower().startswith("move:") and to_id(value.split(":", 1)[1]) in PROTECT_FAMILY:
        return "protected", value.split(":", 1)[1].strip()
    if command == "-boost" and len(parts) > 4:
        return "boosted", f"{value} +{parts[4].strip()}"
    if command == "-heal" and not source.lower().startswith("item:"):
        return "healed", ability
    return None


class ParseState:
    """Everything known so far while reading one log, line by line."""

    def __init__(self) -> None:
        self.roster = Roster()
        self.timeline = Timeline()
        self.field = FieldLedger()
        self.combatants = CombatantLedger(self.roster, self.timeline)
        self.format_id: str | None = None
        self.started_at: int | None = None
        self.frames: list[TurnFrame] = []  # one per turn boundary, for the battle panel
        self.inference_exclusions: set[str] = set()
        self.swapped: set[tuple[str, str]] = set()  # abilities exchanged mid-battle: later reveals are not originals

    @property
    def turn(self) -> int:
        return self.timeline.turn

    def note_ally_effect(self, parts: list[str]) -> None:
        """Record on the open move what it did for the user's own partner: immune, dodged with Protect, an ability
        or stat boost triggered, or healed. Spread moves hit the partner too, often on purpose (ADR-083)."""
        command, move = parts[1], self.timeline.current_move
        if command in _MOVE_BLOCK_ENDS:
            self.timeline.move_open = False
        if not self.timeline.move_open or move is None or command == "move" or len(parts) < 3:
            return
        key = self.roster.resolve(parts[2]) if split_ref(parts[2]) else None
        if key is None or key[0] != move.actor_player or key[1] == move.actor:
            return
        effect = _ally_effect(command, parts)
        if effect is None:
            return
        entry = AllyEffect(species=key[1], effect=effect[0], detail=effect[1])
        if entry not in move.ally_effects:
            move.ally_effects.append(entry)

    def reveal_ability_from(self, parts: list[str]) -> None:
        """An ability named by a ``[from] ability: X`` suffix is X's holder's: the
        ``[of]`` Pokemon when given (Rough Skin hurting its attacker), else the
        line's subject (Protean's type change, Frisk). First reveal wins; the
        ``-ability`` line has its own handler (Trace names the SOURCE there)."""
        if parts[1] == "-ability" or len(parts) < 4:
            return
        source = tag_value(parts[3:], "from")
        if not source.lower().startswith("ability:"):
            return
        ability = source.split(":", 1)[1].strip()
        if to_id(ability) in _THIEF_ABILITIES:
            # The thief is the subject of the -item line; the -enditem line only shows the victim.
            if parts[1] != "-item":
                return
            holder = parts[2]
            self._credit_ability(holder, ability)
            return
        # Absorbing abilities name the ATTACKER in [of]; the Pokemon on the line absorbed the hit.
        holder = parts[2] if to_id(ability) in _ABSORBING_ABILITIES else tag_value(parts[3:], "of") or parts[2]
        self._credit_ability(holder, ability)

    def _credit_ability(self, holder: str, ability: str) -> None:
        key = self.roster.resolve(holder)
        if key is not None:
            mon = self.roster.register(*key)
            if key not in self.swapped:
                mon.ability = mon.ability or ability
            self.combatants.abilities[key] = ability

    def mark_swapped(self, parts: list[str]) -> None:
        """Skill Swap / Wandering Spirit: both Pokemon now hold the other's ability."""
        for ref in (parts[2], tag_value(parts[4:], "of")):
            key = self.roster.resolve(ref) if ref else None
            if key is not None:
                self.swapped.add(key)
        self.inference_exclusions.add("Copied or changed ability needs initial-set attribution")

    def snapshot(self) -> BattleSnapshot:
        """The battle state right now — stamped on every move event."""
        ledger = self.combatants
        mons: dict[str, MonState] = {}
        for player, bucket in self.roster.players.items():
            for key in bucket:
                k = (player, key)
                mons[BattleSnapshot.key(player, key)] = MonState(
                    hp_percent=ledger.hp.get(k), status=ledger.status.get(k, ""),
                    hp_raw=ledger.hp_raw.get(k, ""), ability=ledger.abilities.get(k),
                    hits_taken=ledger.hits_taken.get(k),
                    boosts=dict(ledger.boosts.get(k, {})),
                    item=ledger.held.get(k), forme=ledger.forme.get(k, ""),
                )
        return BattleSnapshot(
            inference_exclusions=sorted(self.inference_exclusions),
            weather=self.field.weather,
            terrain=self.field.terrain,
            screens=self.field.screens(),
            tailwind=self.field.tailwind_now(),
            trick_room=self.field.trick_room_now,
            rooms=self.field.rooms_now(),
            active=ledger.active(),
            fainted={p: list(names) for p, names in ledger.fainted.items()},
            mons=mons,
        )

    def capture_frame(self) -> None:
        """Record every Pokemon's state at the end of the current turn."""
        self.frames.append(TurnFrame.capture(self.turn, self.roster, self.combatants))

    def game_state(self) -> GameState:
        """Close the log and freeze everything into the domain aggregate."""
        self.field.close(self.turn)
        self.timeline.finalize_texts()
        state = GameState(
            started_at=self.started_at,
            inference_exclusions=sorted(self.inference_exclusions),
            turn=self.turn,
            sides=self.roster.sides(),
            outcome=self.timeline.outcome(self.roster),
            field=self.field.conditions(),
        )
        if self.format_id:
            state = state.model_copy(update={"format_id": self.format_id})
        return state
