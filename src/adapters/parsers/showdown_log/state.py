"""The parse-time aggregate shared by every protocol handler."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.combatants import CombatantLedger
from src.adapters.parsers.showdown_log.field_ledger import FieldLedger
from src.adapters.parsers.showdown_log.roster import Roster
from src.adapters.parsers.showdown_log.timeline import Timeline
from src.domain.models import BattleSnapshot, GameState, MonState


class ParseState:
    """Everything known so far while reading one log, line by line."""

    def __init__(self) -> None:
        self.roster = Roster()
        self.timeline = Timeline()
        self.field = FieldLedger()
        self.combatants = CombatantLedger(self.roster, self.timeline)

    @property
    def turn(self) -> int:
        return self.timeline.turn

    def snapshot(self) -> BattleSnapshot:
        """The battle state right now — stamped on every move event."""
        ledger = self.combatants
        mons: dict[str, MonState] = {}
        for player, bucket in self.roster.players.items():
            for key in bucket:
                k = (player, key)
                mons[BattleSnapshot.key(player, key)] = MonState(
                    hp_percent=ledger.hp.get(k), status=ledger.status.get(k, ""),
                    item=ledger.held.get(k),
                )
        return BattleSnapshot(
            weather=self.field.weather,
            terrain=self.field.terrain,
            screens=self.field.screens(),
            active=ledger.active(),
            fainted={p: list(names) for p, names in ledger.fainted.items()},
            mons=mons,
        )

    def game_state(self) -> GameState:
        """Close the log and freeze everything into the domain aggregate."""
        self.field.close(self.turn)
        self.timeline.finalize_texts()
        return GameState(
            turn=self.turn,
            sides=self.roster.sides(),
            outcome=self.timeline.outcome(self.roster),
            field=self.field.conditions(self.combatants.final_statuses()),
        )
