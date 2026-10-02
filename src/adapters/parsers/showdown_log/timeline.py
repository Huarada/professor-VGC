"""The ordered action timeline: events, faints and the battle's outcome.

Also the battle clock (``turn``) and the move currently resolving, since
every result line (damage, block, effectiveness) belongs to that move.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_log.roster import Key, Roster
from src.domain.models import BattleEvent, BattleEventKind, BattleOutcome, KOEvent, TargetHit


class Timeline:
    """Ordered events plus the per-move bookkeeping that annotates them."""

    def __init__(self) -> None:
        self.turn = 0
        self.events: list[BattleEvent] = []
        self.kos: list[KOEvent] = []
        self.fainted_in_move: set[tuple[int, str]] = set()
        self.current_move: BattleEvent | None = None
        self.helped: set[tuple[int, str, str]] = set()  # (turn, player, key) under Helping Hand
        self.winner_name: str | None = None
        self.forfeited_name: str | None = None

    def emit(self, kind: BattleEventKind, key: Key | None, text: str, effects: list[str]) -> None:
        player, actor = key if key else ("", "")
        self.events.append(
            BattleEvent(
                turn=self.turn, kind=kind, actor=actor, actor_player=player,
                effects=effects, text=text,
            )
        )

    def record_faint(self, key: Key) -> None:
        """A KO; also a timeline line unless the move that caused it already says so."""
        player, species = key
        self.kos.append(KOEvent(turn=self.turn, fainted=species, player=player))
        if (self.turn, species) not in self.fainted_in_move:
            self.emit("faint", key, f"{species} ({player}) fainted.", [])

    def add_effect(self, effect: str) -> None:
        if self.current_move is not None and effect not in self.current_move.effects:
            self.current_move.effects.append(effect)

    def record_hit(self, key: Key, hp_after: float | None, blocked_by: str = "") -> None:
        move = self.current_move
        if move is None:
            return
        player, species = key
        hit = TargetHit(player=player, species=species, hp_after=hp_after, blocked_by=blocked_by)
        for i, existing in enumerate(move.hits):
            if (existing.player, existing.species) == key:
                move.hits[i] = hit  # multi-hit move: keep the final HP
                return
        move.hits.append(hit)

    @staticmethod
    def render_move(event: BattleEvent) -> str:
        line = f"{event.actor_player} {event.actor} used {event.move}"
        if event.effects:
            line += f" ({', '.join(event.effects)})"
        if event.results:
            line += " — " + "; ".join(event.results)
        return line

    def finalize_texts(self) -> None:
        for event in self.events:
            if event.kind == "move":
                event.text = self.render_move(event)

    def outcome(self, roster: Roster) -> BattleOutcome:
        winner_player = roster.player_named(self.winner_name)
        forfeited_player = roster.player_named(self.forfeited_name)
        highlights = [
            f"Turn {ko.turn}: {ko.fainted} ({roster.player_names.get(ko.player, ko.player)}) fainted."
            for ko in self.kos
        ]
        if self.forfeited_name:
            highlights.append(
                f"{self.forfeited_name} ({forfeited_player or '?'}) FORFEITED — the game did "
                "NOT end by their team being defeated in play."
            )
        if self.winner_name:
            highlights.append(f"Winner: {self.winner_name} ({winner_player or '?'}).")
        return BattleOutcome(
            winner_player=winner_player,
            winner_name=self.winner_name,
            turns=self.turn,
            kos=self.kos,
            events=self.events,
            highlights=highlights,
            forfeited_player=forfeited_player,
            forfeited_name=self.forfeited_name,
        )
