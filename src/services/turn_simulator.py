"""Turn-by-turn deterministic verification (the per-turn feedback loop).

For every move used, re-consult the engine under the battle state at that
move (``MoveMoment``): projected vs. actual damage, better confirmed moves
into every target, speed order, incoming KO threats and Protect / switch /
speed-control options. One ``TurnCheck`` per action.
"""

from __future__ import annotations

from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CalcEngineAdapter
from src.domain.models import (
    BattleEvent,
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    MoveInfo,
    OptimalMoveOption,
    ProtectRead,
    SpeedComparison,
    TurnCheck,
    TurnDamageCheck,
)
from src.services.battle_moment import BoostLedger, Combatant, MoveMoment
from src.services.decision_review import DecisionReviewer
from src.services.matchup_evaluator import MatchupEvaluator

_BEST_ALTERNATIVES_KEPT = 4


def _parse_percent(text: str) -> float | None:
    """Parse a "43%" (or bare "43") string into 43.0; None if unparseable —
    never lets a malformed log fragment crash the per-turn walk."""
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return None


class _MemoizedCalc:
    """Caches engine answers for one simulation (each call is a Node IPC trip)."""

    def __init__(self, engine: CalcEngineAdapter) -> None:
        self._engine = engine
        self._damage: dict[str, DamageResult | CalcEngineError] = {}
        self._speed: dict[str, SpeedComparison | CalcEngineError] = {}
        self._moves: dict[tuple[int, str], MoveInfo] = {}
        self._formes: dict[tuple[int, str], bool] = {}

    def calculate(self, request: CalcRequest) -> DamageResult:
        key = request.model_dump_json()
        if key not in self._damage:
            try:
                self._damage[key] = self._engine.calculate(request)
            except CalcEngineError as exc:
                self._damage[key] = exc
        cached = self._damage[key]
        if isinstance(cached, CalcEngineError):
            raise cached
        return cached

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        key = request.model_dump_json()
        if key not in self._speed:
            try:
                self._speed[key] = self._engine.compare_speed(request)
            except CalcEngineError as exc:
                self._speed[key] = exc
        cached = self._speed[key]
        if isinstance(cached, CalcEngineError):
            raise cached
        return cached

    def move_info(self, gen: int, move: str) -> MoveInfo:
        key = (gen, move)
        if key not in self._moves:
            self._moves[key] = self._engine.move_info(gen, move)
        return self._moves[key]

    def forme_resolves(self, gen: int, species: str) -> bool:
        key = (gen, species)
        if key not in self._formes:
            self._formes[key] = self._engine.forme_resolves(gen, species)
        return self._formes[key]

    def close(self) -> None:
        self._engine.close()


class TurnReplaySimulator:
    """Re-runs the deterministic engine for each action in the battle timeline."""

    def __init__(self, calc_engine: CalcEngineAdapter, default_gen: int = 9) -> None:
        self._engine = calc_engine
        self._gen = default_gen

    def simulate(self, game_state: GameState, meta: MetaContext) -> list[TurnCheck]:
        """Return one TurnCheck per move actually used (skips switches/faints)."""
        if game_state.outcome is None:
            return []
        calc = _MemoizedCalc(self._engine)
        evaluator = MatchupEvaluator(calc, self._gen)
        reviewer = DecisionReviewer(calc, self._gen)
        sets = evaluator.index_sets(game_state)
        checks: list[TurnCheck] = []

        # Stat stages per (player, species), replayed in event order: a switch
        # resets them, boosts accumulate, each move reads the current value.
        boosts: BoostLedger = {}

        for event in game_state.outcome.events:
            if event.kind == "switch":
                boosts[(event.actor_player, event.actor)] = {}
            elif event.kind == "boost":
                self._apply_boost_event(boosts, event)
            elif event.kind == "move" and event.move:
                moment = MoveMoment(
                    game_state=game_state, event=event, meta=meta, sets=sets,
                    evaluator=evaluator, boosts=boosts, gen=self._gen,
                )
                checks.append(self._check(moment, calc, evaluator, reviewer))
        return checks

    def _check(
        self,
        moment: MoveMoment,
        calc: _MemoizedCalc,
        evaluator: MatchupEvaluator,
        reviewer: DecisionReviewer,
    ) -> TurnCheck:
        event = moment.event
        damaging = evaluator.is_damaging(event.move)
        targets = moment.targets()
        threats = reviewer.threats(moment)
        first_target = targets[0] if targets else None
        return TurnCheck(
            turn=event.turn,
            actor=event.actor,
            actor_player=event.actor_player,
            actor_hp_percent=moment.hp(moment.actor),
            move=event.move,
            effects=list(event.effects),
            conditions=moment.conditions(),
            damage_checks=self._damage_checks(moment, calc, targets) if damaging else [],
            best_alternatives=self._best_alternatives(moment, calc, evaluator),
            incoming_threats=threats,
            decision_options=reviewer.options(moment, threats),
            speed=self._speed_for(moment, calc, first_target),
            note="" if damaging else self._non_damaging_note(event.move),
            stat_caveat=evaluator.forme_caveat(
                moment.attacker,
                moment.combatant(first_target) if first_target else None,
            ),
        )

    @staticmethod
    def _apply_boost_event(ledger: BoostLedger, event: BattleEvent) -> None:
        """Apply one boost event (effects = [stat, signed delta]) to the ledger,
        clamped to -6..+6; a stage back at 0 is removed.
        """
        if len(event.effects) != 2:
            return
        stat, delta_str = event.effects
        try:
            delta = int(delta_str)
        except ValueError:
            return
        stage = ledger.setdefault((event.actor_player, event.actor), {})
        new_value = max(-6, min(6, stage.get(stat, 0) + delta))
        if new_value:
            stage[stat] = new_value
        else:
            stage.pop(stat, None)

    # -- per-move checks ----------------------------------------------------- #

    def _damage_checks(
        self, moment: MoveMoment, calc: _MemoizedCalc, targets: list[Combatant]
    ) -> list[TurnDamageCheck]:
        # Also Pokemon that protected: what a wrong read would have cost.
        actual = self._actual_results(moment.event)
        checks: list[TurnDamageCheck] = []
        for target in targets:
            try:
                dmg = calc.calculate(moment.request(moment.actor, target, moment.event.move))
            except CalcEngineError:
                continue
            actual_text, actual_remaining = actual.get(target) or actual.get(
                ("", target[1]), ("", None)
            )
            checks.append(
                TurnDamageCheck(
                    target=target[1],
                    target_player=target[0],
                    target_hp_before_percent=moment.hp(target),
                    projected_min_percent=dmg.min_percent,
                    projected_max_percent=dmg.max_percent,
                    projected_ko_text=dmg.ko_chance_text,
                    actual_result=actual_text,
                    actual_hp_remaining_percent=actual_remaining,
                    description=dmg.description,
                )
            )
        return checks

    def _best_alternatives(
        self, moment: MoveMoment, calc: _MemoizedCalc, evaluator: MatchupEvaluator
    ) -> list[OptimalMoveOption]:
        """Every confirmed damaging move into every opposing active Pokemon, so a
        better move and a better target both surface.
        """
        candidates: list[OptimalMoveOption] = []
        opponents = moment.opponents()
        for move in dict.fromkeys(moment.attacker.moves):
            if not evaluator.is_damaging(move):
                continue
            for target in opponents:
                try:
                    dmg = calc.calculate(moment.request(moment.actor, target, move))
                except CalcEngineError:
                    continue
                candidates.append(
                    OptimalMoveOption(
                        move=move,
                        target=target[1],
                        min_percent=dmg.min_percent,
                        max_percent=dmg.max_percent,
                        ko_chance_text=dmg.ko_chance_text,
                        is_ko_guaranteed=dmg.is_ko_guaranteed,
                        description=dmg.description,
                    )
                )
        candidates.sort(key=lambda c: (c.is_ko_guaranteed, c.max_percent), reverse=True)
        return candidates[:_BEST_ALTERNATIVES_KEPT]

    @staticmethod
    def _speed_for(
        moment: MoveMoment, calc: _MemoizedCalc, target: Combatant | None
    ) -> SpeedComparison | None:
        if target is None:
            return None
        try:
            return calc.compare_speed(moment.request(moment.actor, target, "Tackle"))
        except CalcEngineError:
            return None

    @staticmethod
    def _actual_results(event: BattleEvent) -> dict[Combatant, tuple[str, float | None]]:
        """target -> (actual_result text, HP left as a number; None if protected),
        from one source so they cannot drift (ADR-029).
        """
        mapping: dict[Combatant, tuple[str, float | None]] = {}
        if event.hits:
            for hit in event.hits:
                key = (hit.player, hit.species)
                if hit.blocked_by:
                    mapping[key] = (f"blocked ({hit.blocked_by})", None)
                elif hit.hp_after == 0.0:
                    mapping[key] = ("fainted (KO)", 0.0)
                elif hit.hp_after is not None:
                    mapping[key] = (f"ended at {hit.hp_after:g}% HP", hit.hp_after)
            return mapping
        for result in event.results:
            if " blocked (" in result:
                name, tail = result.split(" blocked (", 1)
                mapping[("", name.strip())] = (f"blocked ({tail}", None)
            elif "->" in result:
                name, pct = result.split("->", 1)
                pct = pct.strip()
                mapping[("", name.strip())] = (f"ended at {pct} HP", _parse_percent(pct))
            elif result.endswith("fainted"):
                mapping[("", result.replace("fainted", "").strip())] = ("fainted (KO)", 0.0)
        return mapping

    @staticmethod
    def _non_damaging_note(move: str) -> str:
        return f"'{move}' is non-damaging (status/setup); no damage projected."

    # -- Protect reads ------------------------------------------------------- #

    def build_protect_reads(
        self, checks: list[TurnCheck], game_state: GameState
    ) -> list[ProtectRead]:
        """Classify every Protect-family block in ``checks``.

        Runs after :meth:`simulate` (a teammate's same-turn faint lives in another
        TurnCheck); derived data only, no engine calls.
        """
        side_of = game_state.side_of()
        brought_by_player = {
            side.player: set(side.brought() or [mon.species for mon in side.team])
            for side in game_state.sides
        }
        reads: list[ProtectRead] = []
        for check in checks:
            for dmg in check.damage_checks:
                if not dmg.actual_result.startswith("blocked ("):
                    continue
                other_targets_hit = [
                    d for d in check.damage_checks
                    if d is not dmg and not d.actual_result.startswith("blocked (")
                ]
                is_spread_move = "spread" in check.effects
                was_immediate_ko_threat = "OHKO" in dmg.projected_ko_text
                blocker_player = dmg.target_player or self._resolve_blocker_player(
                    dmg.target, check.actor_player, brought_by_player, side_of
                )
                teammate_fainted = self._teammate_fainted_this_turn(
                    checks, side_of, check.turn, dmg.target, blocker_player
                )
                reads.append(
                    ProtectRead(
                        turn=check.turn,
                        blocker=dmg.target,
                        blocker_player=blocker_player,
                        attacker=check.actor,
                        attacker_player=check.actor_player,
                        move=check.move,
                        is_spread_move=is_spread_move,
                        value_denied=dmg,
                        other_targets_hit=other_targets_hit,
                        is_genuine_read=not is_spread_move,
                        was_immediate_ko_threat=was_immediate_ko_threat,
                        misallocated=not was_immediate_ko_threat and bool(teammate_fainted),
                        teammate_fainted=teammate_fainted,
                    )
                )
        return reads

    @staticmethod
    def _resolve_blocker_player(
        blocker: str,
        attacker_player: str,
        brought_by_player: dict[str, set[str]],
        side_of: dict[str, str],
    ) -> str:
        """Player who brought ``blocker``; in a mirror, the side opposite the attacker."""
        for player, names in brought_by_player.items():
            if player != attacker_player and blocker in names:
                return player
        return side_of.get(blocker, "")

    @staticmethod
    def _teammate_fainted_this_turn(
        checks: list[TurnCheck], side_of: dict[str, str], turn: int, blocker: str,
        blocker_player: str,
    ) -> str:
        """Species that fainted THIS turn on the blocker's own side, if any."""
        for check in checks:
            if check.turn != turn:
                continue
            for dmg in check.damage_checks:
                owner = dmg.target_player or side_of.get(dmg.target)
                if (
                    (dmg.target, owner) != (blocker, blocker_player)
                    and owner == blocker_player
                    and "fainted" in dmg.actual_result
                ):
                    return dmg.target
        return ""
