"""The parse-time aggregate shared by every protocol handler."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.combatants import CombatantLedger
from src.adapters.parsers.showdown_log.field_ledger import FieldLedger
from src.adapters.parsers.showdown_log.protocol import tag_value, to_id
from src.adapters.parsers.showdown_log.replay_frames import TurnFrame
from src.adapters.parsers.showdown_log.roster import Roster
from src.adapters.parsers.showdown_log.timeline import Timeline
from src.domain.models import BattleSnapshot, GameState, MonState


# Their `-heal`/`-immune`/`-boost` lines carry the attacker (the effect source) in [of].
_ABSORBING_ABILITIES = frozenset({
    "waterabsorb", "voltabsorb", "dryskin", "sapsipper", "stormdrain", "lightningrod", "motordrive",
    "eartheater", "wellbakedbody", "windrider", "flashfire"})
# Pickpocket/Magician: [of] is the victim, the ability belongs to the thief.
_THIEF_ABILITIES = frozenset({"pickpocket", "magician"})


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
