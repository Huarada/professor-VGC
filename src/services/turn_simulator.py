"""Turn-by-turn deterministic verification (the per-turn feedback loop).

For EVERY move actually used in the battle, re-consult the deterministic engine
under THAT turn's field conditions: projected damage for the move that was used
against the real target(s), the field-aware speed order, and the conditions in
effect. This produces a :class:`~src.domain.models.TurnCheck` per action so the
explanation AI validates each turn against ground truth instead of the whole
game being summarized once.
"""

from __future__ import annotations

from typing import Any

from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CalcEngineAdapter
from src.domain.models import (
    BattleEvent,
    CalcRequest,
    GameState,
    MetaContext,
    OptimalMoveOption,
    PokemonSet,
    ProtectRead,
    SpeedComparison,
    TurnCheck,
    TurnDamageCheck,
)
from src.services.matchup_evaluator import MatchupEvaluator

_BoostLedger = dict[tuple[str, str], dict[str, int]]

_BEST_ALTERNATIVES_KEPT = 4

# Non-damaging (0 base power) moves we should not try to run through the
# damage calc. Fake Out (40 BP Normal) and Matcha Gotcha (80 BP Grass) are
# NOT status moves despite reading like utility/support moves at a glance —
# both deal real damage and MUST go through the calc like any other attack.
# Silently skipping them here used to mean best_alternatives/damage_checks
# never computed a real figure for either: a Fake Out candidate against a
# Ghost-type (immune to Normal) was simply absent instead of coming back as
# the correct "0%, no effect" from @smogon/calc — leaving the explanation
# model with no grounded data to catch a bad suggestion like "use Fake Out"
# against a Ghost-type target. Fixed here rather than in the prompt: this
# project's own rule is to push a judgment call into deterministic code
# whenever possible (see ADR-010), and immunity is exactly that.
_STATUS_MOVES = {
    "tailwind", "protect", "willowisp", "thunderwave", "reflect", "lightscreen",
    "trickroom", "helpinghand", "ragepowder", "followme", "swordsdance",
    "nastyplot", "calmmind", "dragondance", "strengthsap", "lifedew", "spore",
    "sleeppowder", "partingshot",
}


def _norm_move(move: str) -> str:
    return move.lower().replace(" ", "").replace("-", "").replace("'", "")


def _parse_percent(text: str) -> float | None:
    """Parse a "43%" (or bare "43") string into 43.0; None if unparseable —
    never lets a malformed log fragment crash the per-turn walk."""
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return None


class TurnReplaySimulator:
    """Re-runs the deterministic engine for each action in the battle timeline."""

    def __init__(self, calc_engine: CalcEngineAdapter, default_gen: int = 9) -> None:
        self._calc = calc_engine
        self._gen = default_gen
        self._evaluator = MatchupEvaluator(calc_engine, default_gen)

    def simulate(self, game_state: GameState, meta: MetaContext) -> list[TurnCheck]:
        """Return one TurnCheck per move actually used (skips switches/faints)."""
        if game_state.outcome is None:
            return []
        sets = self._evaluator.index_sets(game_state)
        statuses = game_state.field.statuses if game_state.field else {}
        side_of = game_state.side_of()
        checks: list[TurnCheck] = []

        # Running per-Pokemon stat-stage ledger, keyed by (player, species).
        # NOT a fixed attribute like ability/item — mutated in strict
        # chronological order as the SAME ordered events stream by (switch
        # resets it; "boost"/"-unboost" events, parsed by showdown_parser
        # into kind="boost" BattleEvents, accumulate it), matching the real
        # in-game mechanic that stages reset the moment a Pokemon leaves the
        # field. Every calc built below for a "move" event reads this
        # ledger exactly as it stands at that point in the walk — e.g. an
        # Intimidate that happened on an earlier switch-in this same turn
        # is already applied by the time a later move this turn is checked.
        boost_ledger: dict[tuple[str, str], dict[str, int]] = {}

        for event in game_state.outcome.events:
            if event.kind == "switch":
                boost_ledger[(event.actor_player, event.actor)] = {}
                continue
            if event.kind == "boost":
                self._apply_boost_event(boost_ledger, event)
                continue
            if event.kind != "move" or not event.move:
                continue

            attacker_boosts = boost_ledger.get((event.actor_player, event.actor), {})
            attacker = self._evaluator.enrich_set(
                sets.get(event.actor) or PokemonSet(species=event.actor), meta, statuses,
                boosts=attacker_boosts,
            )
            field = self._field_for_turn(game_state, event.actor_player, event.turn, side_of, event)
            conditions = self._condition_labels(game_state, event.actor_player, event.turn, event)

            damage_checks = self._damage_checks(
                event, attacker, sets, meta, statuses, field, side_of, boost_ledger
            )
            best_alternatives = self._best_alternatives(
                event, attacker, sets, meta, statuses, field, side_of, boost_ledger
            )
            speed = self._speed_for(
                event, attacker, sets, meta, statuses, field, side_of, boost_ledger
            )
            note = "" if damage_checks else self._non_damaging_note(event.move)
            stat_caveat = self._stat_caveat(
                event, attacker, sets, meta, statuses, side_of, boost_ledger
            )

            checks.append(
                TurnCheck(
                    turn=event.turn,
                    actor=event.actor,
                    actor_player=event.actor_player,
                    move=event.move,
                    effects=list(event.effects),
                    conditions=conditions,
                    damage_checks=damage_checks,
                    best_alternatives=best_alternatives,
                    speed=speed,
                    note=note,
                    stat_caveat=stat_caveat,
                )
            )
        return checks

    @staticmethod
    def _apply_boost_event(ledger: _BoostLedger, event: BattleEvent) -> None:
        """Applies one kind="boost" BattleEvent (effects = [stat, signed
        delta as str]) to the running ledger in place, clamped to the real
        -6..+6 stage range and dropped back out of the dict entirely at
        exactly 0 (an explicit {} / no-key state, same as "never boosted",
        rather than an explicit {"atk": 0} that would still read as a
        confirmed-but-neutral entry)."""
        if len(event.effects) != 2:
            return
        stat, delta_str = event.effects
        try:
            delta = int(delta_str)
        except ValueError:
            return
        key = (event.actor_player, event.actor)
        stage = ledger.setdefault(key, {})
        new_value = max(-6, min(6, stage.get(stat, 0) + delta))
        if new_value:
            stage[stat] = new_value
        else:
            stage.pop(stat, None)

    def build_protect_reads(
        self, checks: list[TurnCheck], game_state: GameState
    ) -> list[ProtectRead]:
        """Classify every Protect-family block found in ``checks``.

        Runs AFTER :meth:`simulate` (needs the full turn list to check
        whether a teammate fainted the SAME turn, which lives in a different
        TurnCheck than the block itself). Purely a derived read over data
        `simulate` already computed — no new engine calls.

        ``GameState.side_of()`` is a global species -> single player map, so
        a mirror match (the same species brought on BOTH sides — not rare in
        VGC) silently collapses to whichever side it saw first. A block is
        virtually always against the OPPOSING side's move, so the blocker's
        player is resolved locally against ``brought_by_player`` first
        (disambiguating the mirror case), falling back to the lossy global
        map only when that's inconclusive.
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
                blocker_player = self._resolve_blocker_player(
                    dmg.target, check.actor_player, brought_by_player, side_of
                )
                teammate_fainted = self._teammate_fainted_this_turn(
                    checks, side_of, check.turn, dmg.target, blocker_player
                )
                misallocated = not was_immediate_ko_threat and bool(teammate_fainted)
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
                        misallocated=misallocated,
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
        """Player that actually brought ``blocker``, disambiguating a mirror
        match (same species on both sides) toward the side OPPOSITE the
        attacker — a Protect block is virtually always against an opposing
        move. Falls back to the global (lossy in the mirror case) side_of
        map when the species isn't unambiguously resolvable this way."""
        opposing = [p for p in brought_by_player if p != attacker_player]
        for player in opposing:
            if blocker in brought_by_player.get(player, ()):
                return player
        return side_of.get(blocker, "")

    @staticmethod
    def _teammate_fainted_this_turn(
        checks: list[TurnCheck], side_of: dict[str, str], turn: int, blocker: str, blocker_player: str
    ) -> str:
        """Species that fainted THIS turn on the blocker's own side, if any."""
        for check in checks:
            if check.turn != turn:
                continue
            for dmg in check.damage_checks:
                if (
                    dmg.target != blocker
                    and side_of.get(dmg.target) == blocker_player
                    and "fainted" in dmg.actual_result
                ):
                    return dmg.target
        return ""

    # -- helpers --------------------------------------------------------- #

    def _damage_checks(
        self,
        event: BattleEvent,
        attacker: PokemonSet,
        sets: dict[str, PokemonSet],
        meta: MetaContext,
        statuses: dict[str, str],
        field: dict[str, Any],
        side_of: dict[str, str],
        boost_ledger: _BoostLedger,
    ) -> list[TurnDamageCheck]:
        if _norm_move(event.move) in _STATUS_MOVES:
            return []
        result_by_target = self._actual_results(event)
        checks: list[TurnDamageCheck] = []
        # Include Pokemon that BLOCKED this move with Protect/Detect/etc: the
        # projected damage here is the deterministic "what a wrong read would
        # have cost" figure a risk/reward analysis of the turn needs.
        all_targets = list(event.targets) + [
            b for b in event.blocked if b not in event.targets
        ]
        for target_name in all_targets:
            defender_boosts = boost_ledger.get((side_of.get(target_name, ""), target_name), {})
            defender = self._evaluator.enrich_set(
                sets.get(target_name) or PokemonSet(species=target_name), meta, statuses,
                boosts=defender_boosts,
            )
            try:
                dmg = self._calc.calculate(
                    CalcRequest(
                        gen=self._gen, attacker=attacker, defender=defender,
                        move=event.move, field=field,
                    )
                )
            except CalcEngineError:
                continue
            actual_text, actual_remaining = result_by_target.get(target_name, ("", None))
            checks.append(
                TurnDamageCheck(
                    target=target_name,
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
        self,
        event: BattleEvent,
        attacker: PokemonSet,
        sets: dict[str, PokemonSet],
        meta: MetaContext,
        statuses: dict[str, str],
        field: dict[str, Any],
        side_of: dict[str, str],
        boost_ledger: _BoostLedger,
    ) -> list[OptimalMoveOption]:
        """Re-consult the engine for EVERY other move confirmed for this Pokemon
        this game, against the same real target/field, to validate the optimal
        play that turn instead of only checking the move actually used.
        """
        if not event.targets:
            return []
        target_name = event.targets[0]
        defender_boosts = boost_ledger.get((side_of.get(target_name, ""), target_name), {})
        defender = self._evaluator.enrich_set(
            sets.get(target_name) or PokemonSet(species=target_name), meta, statuses,
            boosts=defender_boosts,
        )
        candidates: list[OptimalMoveOption] = []
        seen_moves: set[str] = set()
        for move in attacker.moves:
            if move in seen_moves or _norm_move(move) in _STATUS_MOVES:
                continue
            seen_moves.add(move)
            try:
                dmg = self._calc.calculate(
                    CalcRequest(
                        gen=self._gen, attacker=attacker, defender=defender,
                        move=move, field=field,
                    )
                )
            except CalcEngineError:
                continue
            candidates.append(
                OptimalMoveOption(
                    move=move,
                    target=target_name,
                    min_percent=dmg.min_percent,
                    max_percent=dmg.max_percent,
                    ko_chance_text=dmg.ko_chance_text,
                    is_ko_guaranteed=dmg.is_ko_guaranteed,
                    description=dmg.description,
                )
            )
        candidates.sort(key=lambda c: (c.is_ko_guaranteed, c.max_percent), reverse=True)
        return candidates[:_BEST_ALTERNATIVES_KEPT]

    def _stat_caveat(
        self,
        event: BattleEvent,
        attacker: PokemonSet,
        sets: dict[str, PokemonSet],
        meta: MetaContext,
        statuses: dict[str, str],
        side_of: dict[str, str],
        boost_ledger: _BoostLedger,
    ) -> str:
        if not event.targets:
            return self._evaluator.forme_caveat(attacker)
        target_name = event.targets[0]
        defender_boosts = boost_ledger.get((side_of.get(target_name, ""), target_name), {})
        defender = self._evaluator.enrich_set(
            sets.get(target_name) or PokemonSet(species=target_name), meta, statuses,
            boosts=defender_boosts,
        )
        return self._evaluator.forme_caveat(attacker, defender)

    @staticmethod
    def _actual_results(event: BattleEvent) -> dict[str, tuple[str, float | None]]:
        """Maps species -> (the human-readable actual_result text, the SAME
        fact again as a plain float — remaining HP percent, 0.0 on a faint,
        None for a Protect block, where no HP changed). One parse of
        event.results produces both, so the text and the number can never
        drift out of sync with each other (see ADR-029: the explanation
        model no longer has to derive "remaining HP" out of a string it
        wasn't given as a number in the first place)."""
        mapping: dict[str, tuple[str, float | None]] = {}
        for result in event.results:
            if " blocked (" in result:
                name, tail = result.split(" blocked (", 1)
                mapping[name.strip()] = (f"blocked ({tail}", None)
            elif "->" in result:
                name, pct = result.split("->", 1)
                pct = pct.strip()
                mapping[name.strip()] = (f"ended at {pct} HP", _parse_percent(pct))
            elif result.endswith("fainted"):
                mapping[result.replace("fainted", "").strip()] = ("fainted (KO)", 0.0)
        return mapping

    def _speed_for(
        self,
        event: BattleEvent,
        attacker: PokemonSet,
        sets: dict[str, PokemonSet],
        meta: MetaContext,
        statuses: dict[str, str],
        field: dict[str, Any],
        side_of: dict[str, str],
        boost_ledger: _BoostLedger,
    ) -> SpeedComparison | None:
        if not event.targets:
            return None
        target_name = event.targets[0]
        defender_boosts = boost_ledger.get((side_of.get(target_name, ""), target_name), {})
        defender = self._evaluator.enrich_set(
            sets.get(target_name) or PokemonSet(species=target_name), meta, statuses,
            boosts=defender_boosts,
        )
        try:
            return self._calc.compare_speed(
                CalcRequest(
                    gen=self._gen, attacker=attacker, defender=defender,
                    move="Tackle", field=field,
                )
            )
        except CalcEngineError:
            return None

    @staticmethod
    def _field_for_turn(
        game_state: GameState,
        actor_player: str,
        turn: int,
        side_of: dict[str, str],
        event: BattleEvent,
    ) -> dict[str, Any]:
        field = game_state.field
        if field is None:
            return {}
        spec: dict[str, Any] = {}
        if actor_player and field.tailwind_active(actor_player, turn):
            spec["attackerTailwind"] = True
        # defender side = the opposite player (first target's owner if known)
        defender_player = None
        if event.targets:
            defender_player = side_of.get(event.targets[0])
        if defender_player and field.tailwind_active(defender_player, turn):
            spec["defenderTailwind"] = True
        if field.trick_room_active(turn):
            spec["trickRoom"] = True
        if field.weather:
            spec["weather"] = field.weather
        return spec

    @staticmethod
    def _condition_labels(
        game_state: GameState, actor_player: str, turn: int, event: BattleEvent
    ) -> list[str]:
        field = game_state.field
        if field is None:
            return []
        labels: list[str] = []
        for side in game_state.sides:
            if field.tailwind_active(side.player, turn):
                labels.append(f"Tailwind {side.player}")
        if field.trick_room_active(turn):
            labels.append("Trick Room")
        if field.weather:
            labels.append(f"weather {field.weather}")
        return labels

    @staticmethod
    def _non_damaging_note(move: str) -> str:
        return f"'{move}' is non-damaging (status/setup); no damage projected."
