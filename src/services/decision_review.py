"""Deterministic review of the NON-attacking side of a VGC decision.

"Which move hits hardest" is only half of a turn. The other half is whether
the Pokemon was in KO range at all and, if so, whether Protect, a switch or
speed control would have answered that threat. This module answers those
questions with the calc engine — never with the LLM — and only from facts
confirmed this game:

- threats come from opposing Pokemon ON THE FIELD at that move, using moves
  they were seen using this game, at the actor's real HP;
- Protect / speed-control options only use moves the actor was seen using;
- switch options only use Pokemon actually brought and still able to switch.

Options are produced only when some threat could KO the actor that turn, so
a safe turn never gets "you should have protected" noise.
"""

from __future__ import annotations

from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CalcEngineAdapter
from src.domain.models import (
    DamageResult,
    DecisionKind,
    DecisionOption,
    MoveInfo,
    SpeedComparison,
    ThreatCheck,
)
from src.services.battle_moment import Combatant, MoveMoment

_MAX_SWITCH_OPTIONS = 2


def _pct(value: float) -> str:
    return f"{value:g}%"


class DecisionReviewer:
    """Computes ``incoming_threats`` and ``decision_options`` for one move."""

    def __init__(self, calc_engine: CalcEngineAdapter, gen: int = 9) -> None:
        self._calc = calc_engine
        self._gen = gen

    # -- engine helpers ------------------------------------------------------ #

    def _info(self, move: str) -> MoveInfo:
        try:
            return self._calc.move_info(self._gen, move)
        except CalcEngineError:
            return MoveInfo(name=move)

    def _best_hit(
        self, moment: MoveMoment, attacker: Combatant, defender: Combatant
    ) -> tuple[str, DamageResult] | None:
        """``attacker``'s strongest confirmed attack into ``defender`` right now."""
        best: tuple[str, DamageResult] | None = None
        for move in dict.fromkeys(moment.combatant(attacker).moves):
            if not self._info(move).is_damaging:
                continue
            try:
                result = self._calc.calculate(moment.request(attacker, defender, move))
            except CalcEngineError:
                continue
            if best is None or (result.is_ko_guaranteed, result.max_percent) > (
                best[1].is_ko_guaranteed, best[1].max_percent
            ):
                best = (move, result)
        return best

    def _speed(
        self, moment: MoveMoment, threat: Combatant, actor: Combatant, *,
        actor_tailwind: bool = False, flip_trick_room: bool = False,
        threat_speed_drop: int = 0, threat_paralyzed: bool = False,
    ) -> SpeedComparison | None:
        """Speed order of ``threat`` (as attacker) vs ``actor``, optionally
        under a hypothetical speed-control effect."""
        request = moment.request(threat, actor, "Tackle")
        field = request.field
        if actor_tailwind:
            field = field.model_copy(
                update={"defender_side": field.defender_side.model_copy(update={"tailwind": True})}
            )
        if flip_trick_room:
            field = field.model_copy(update={"trick_room": not field.trick_room})
        threat_set = request.attacker
        if threat_speed_drop:
            boosts = dict(threat_set.boosts)
            boosts["spe"] = max(-6, boosts.get("spe", 0) - threat_speed_drop)
            threat_set = threat_set.model_copy(update={"boosts": boosts})
        if threat_paralyzed and not threat_set.status:
            threat_set = threat_set.model_copy(update={"status": "par"})
        try:
            return self._calc.compare_speed(
                request.model_copy(update={"attacker": threat_set, "field": field})
            )
        except CalcEngineError:
            return None

    @staticmethod
    def _threat_moves_first(
        speed: SpeedComparison | None, threat: Combatant, actor: Combatant
    ) -> bool | None:
        if speed is None or speed.is_tie or threat[1] == actor[1]:
            return None  # a tie, or a mirror match the names can't disambiguate
        return speed.faster == threat[1]

    # -- public API ---------------------------------------------------------- #

    def threats(self, moment: MoveMoment) -> list[ThreatCheck]:
        """The strongest confirmed attack each opposing active had into the actor."""
        actor = moment.actor
        actor_hp = moment.hp(actor)
        found: list[ThreatCheck] = []
        for opponent in moment.opponents():
            best = self._best_hit(moment, opponent, actor)
            if best is None:
                continue
            move, result = best
            found.append(
                ThreatCheck(
                    attacker=opponent[1],
                    attacker_player=opponent[0],
                    move=move,
                    min_percent=result.min_percent,
                    max_percent=result.max_percent,
                    ko_chance_text=result.ko_chance_text,
                    can_ko=result.max_percent >= (actor_hp if actor_hp is not None else 100.0),
                    moves_first=self._threat_moves_first(
                        self._speed(moment, opponent, actor), opponent, actor
                    ),
                )
            )
        found.sort(key=lambda t: t.max_percent, reverse=True)
        return found

    def options(self, moment: MoveMoment, threats: list[ThreatCheck]) -> list[DecisionOption]:
        """Protect / switch / speed-control plays that answer a KO threat."""
        lethal = [t for t in threats if t.can_ko]
        if not lethal:
            return []
        actor_moves = list(dict.fromkeys(moment.attacker.moves))
        infos = {move: self._info(move) for move in actor_moves}
        options: list[DecisionOption] = []
        options.extend(self._protect_options(moment, lethal[0], infos))
        options.extend(self._switch_options(moment, lethal))
        options.extend(self._speed_control_options(moment, lethal, infos))
        return options

    # -- option builders ----------------------------------------------------- #

    def _protect_options(
        self, moment: MoveMoment, threat: ThreatCheck, infos: dict[str, MoveInfo]
    ) -> list[DecisionOption]:
        if infos.get(moment.event.move, self._info(moment.event.move)).is_protect:
            return []  # it already protected
        hp = moment.hp(moment.actor)
        hp_text = f" from {_pct(hp)} HP" if hp is not None else ""
        return [
            DecisionOption(
                kind=DecisionKind.PROTECT,
                move=move,
                against=threat.attacker,
                summary=(
                    f"{move} (confirmed in {moment.actor[1]}'s moveset this game) would have "
                    f"blocked {threat.attacker}'s {threat.move} "
                    f"({_pct(threat.min_percent)}-{_pct(threat.max_percent)}, "
                    f"enough to KO{hp_text})."
                ),
            )
            for move, info in infos.items()
            if info.is_protect
        ][:1]

    def _switch_options(
        self, moment: MoveMoment, lethal: list[ThreatCheck]
    ) -> list[DecisionOption]:
        player = moment.actor[0]
        candidates: list[tuple[float, str, ThreatCheck, str]] = []
        for name in moment.bench(player):
            switch_in: Combatant = (player, name)
            worst: tuple[float, ThreatCheck, str] | None = None
            for threat in lethal:
                best = self._best_hit(moment, (threat.attacker_player, threat.attacker), switch_in)
                if best is None:
                    continue
                if worst is None or best[1].max_percent > worst[0]:
                    worst = (best[1].max_percent, threat, best[0])
            if worst is None:
                continue
            hp = moment.hp(switch_in)
            if worst[0] < (hp if hp is not None else 100.0):
                candidates.append((worst[0], name, worst[1], worst[2]))
        candidates.sort(key=lambda c: c[0])
        return [
            DecisionOption(
                kind=DecisionKind.SWITCH,
                switch_to=name,
                against=threat.attacker,
                max_percent_taken=taken,
                summary=(
                    f"Switching {moment.actor[1]} out to {name} would have taken at most "
                    f"{_pct(taken)} from {threat.attacker}'s {move} instead of risking a KO "
                    f"on {moment.actor[1]}."
                ),
            )
            for taken, name, threat, move in candidates[:_MAX_SWITCH_OPTIONS]
        ]

    def _speed_control_options(
        self, moment: MoveMoment, lethal: list[ThreatCheck], infos: dict[str, MoveInfo]
    ) -> list[DecisionOption]:
        outsped = [t for t in lethal if t.moves_first]
        if not outsped:
            return []
        options: list[DecisionOption] = []
        for move, info in infos.items():
            if not info.speed_control or move == moment.event.move:
                continue
            for threat in outsped:
                threat_mon: Combatant = (threat.attacker_player, threat.attacker)
                speed = self._speed(
                    moment, threat_mon, moment.actor,
                    actor_tailwind=info.speed_control == "tailwind",
                    flip_trick_room=info.speed_control == "trick_room",
                    threat_speed_drop=info.speed_drop_stages if info.speed_control == "speed_drop" else 0,
                    threat_paralyzed=info.speed_control == "paralysis",
                )
                if speed is None or self._threat_moves_first(
                    speed, threat_mon, moment.actor
                ) is not False:
                    continue
                options.append(
                    DecisionOption(
                        kind=DecisionKind.SPEED_CONTROL,
                        move=move,
                        against=threat.attacker,
                        summary=(
                            f"{move} (confirmed in {moment.actor[1]}'s moveset this game) "
                            f"flips the speed order: {moment.actor[1]} would move before "
                            f"{threat.attacker} on the following turns "
                            f"({speed.faster_speed} vs {speed.slower_speed} effective Speed)."
                        ),
                    )
                )
        return options
