"""Tests for the non-attacking side of each decision (incoming threats and
Protect / switch / speed-control options) and for retargeting.

Reported: "best alternatives" only compared the damage of moves already seen
against the SAME target — never Protect, switching, a different target or
speed control, which are the decisions that define VGC. These are now
engine-verified per move, from facts confirmed this game only.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import (
    CalcRequest,
    DamageResult,
    DecisionKind,
    GameState,
    MetaContext,
    MoveInfo,
    PokemonSet,
    SpeedComparison,
    TurnCheck,
)
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import fake_move_info

_DAMAGE = {
    ("Garchomp", "Talonflame", "Rock Slide"): 60.0,
    ("Garchomp", "Amoonguss", "Rock Slide"): 20.0,
    ("Garchomp", "Incineroar", "Rock Slide"): 30.0,
    ("Talonflame", "Garchomp", "Brave Bird"): 50.0,
    ("Talonflame", "Rillaboom", "Brave Bird"): 95.0,
}
_SPEED = {"Garchomp": 150, "Talonflame": 120, "Rillaboom": 85, "Incineroar": 60, "Amoonguss": 30}


class _ScriptedCalc:
    """Damage from a table (OHKO when it covers the target's current HP) and
    speed from base numbers with Tailwind / stages / paralysis / Trick Room."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        pct = _DAMAGE.get(
            (request.attacker.species, request.defender.species, request.move), 10.0
        )
        hp = request.defender_hp_percent if request.defender_hp_percent is not None else 100.0
        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, damage_rolls=[1], min_percent=round(pct * 0.85, 1),
            max_percent=pct, ko_chance_text="guaranteed OHKO" if pct * 0.85 >= hp else
            ("chance to OHKO" if pct >= hp else "guaranteed 2HKO"),
            is_ko_guaranteed=pct * 0.85 >= hp,
        )

    @staticmethod
    def _speed(mon: PokemonSet, tailwind: bool) -> int:
        speed = float(_SPEED.get(mon.species, 100))
        stage = mon.boosts.get("spe", 0)
        speed *= (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)
        if tailwind:
            speed *= 2
        if mon.status == "par":
            speed /= 2
        return int(speed)

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        a = self._speed(request.attacker, request.field.attacker_side.tailwind)
        d = self._speed(request.defender, request.field.defender_side.tailwind)
        attacker_first = (a > d) != request.field.trick_room
        fast, slow = (
            (request.attacker.species, a, request.defender.species, d)[::2],
            (request.attacker.species, a, request.defender.species, d)[1::2],
        )
        if not attacker_first:
            fast, slow = fast[::-1], slow[::-1]
        return SpeedComparison(
            faster=fast[0], slower=fast[1], faster_speed=slow[0], slower_speed=slow[1],
            is_tie=a == d, trick_room=request.field.trick_room,
        )

    def move_info(self, gen: int, move: str) -> MoveInfo:
        return fake_move_info(move)

    def forme_resolves(self, gen: int, species: str) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover
        pass


_LOG = "\n".join([
    "|player|p1|Ash|", "|player|p2|Gary|", "|start",
    "|switch|p1a: Talonflame|Talonflame, L50|100/100",
    "|switch|p1b: Incineroar|Incineroar, L50|100/100",
    "|switch|p2a: Garchomp|Garchomp, L50|100/100",
    "|switch|p2b: Rillaboom|Rillaboom, L50|100/100",
    "|turn|1",
    "|move|p1a: Talonflame|Tailwind|p1a: Talonflame",
    "|-sidestart|p1: Ash|move: Tailwind",
    "|move|p2a: Garchomp|Rock Slide|p1a: Talonflame|[spread] p1a,p1b",
    "|-damage|p1a: Talonflame|40/100",
    "|-damage|p1b: Incineroar|70/100",
    "|turn|2",
    "|move|p1a: Talonflame|Protect|p1a: Talonflame",
    "|move|p2a: Garchomp|Rock Slide|p1a: Talonflame|[spread] p1a,p1b",
    "|-activate|p1a: Talonflame|move: Protect",
    "|-damage|p1b: Incineroar|40/100",
    "|-sideend|p1: Ash|move: Tailwind",
    "|turn|3",
    "|move|p2a: Garchomp|Rock Slide|p1a: Talonflame|[spread] p1a,p1b",
    "|-damage|p1a: Talonflame|0 fnt",
    "|-damage|p1b: Incineroar|10/100",
    "|faint|p1a: Talonflame",
    "|turn|4",
    "|move|p1b: Incineroar|Fake Out|p2a: Garchomp",
    "|-damage|p2a: Garchomp|90/100",
    "|switch|p1a: Amoonguss|Amoonguss, L50|100/100",
])
# Turn 3's Brave Bird never happens in the log above (Talonflame fainted
# first), so a separate log checks the "actor moved while in KO range" case.
_LOG_ATTACKED = _LOG.replace(
    "|turn|3\n",
    "|turn|3\n|move|p1a: Talonflame|Brave Bird|p2a: Garchomp\n|-damage|p2a: Garchomp|50/100\n",
)


def _checks(log: str) -> tuple[GameState, list[TurnCheck]]:
    state = ShowdownReplayParser().parse(log)
    return state, TurnReplaySimulator(_ScriptedCalc()).simulate(state, MetaContext())


def _talonflame_turn(checks: list[TurnCheck], turn: int) -> TurnCheck:
    return next(c for c in checks if c.turn == turn and c.actor == "Talonflame")


def test_incoming_threat_uses_the_actors_real_hp():
    _, checks = _checks(_LOG_ATTACKED)
    turn3 = _talonflame_turn(checks, 3)
    assert turn3.actor_hp_percent == 40.0
    threat = turn3.incoming_threats[0]
    assert (threat.attacker, threat.attacker_player, threat.move) == ("Garchomp", "p2", "Rock Slide")
    assert threat.can_ko is True  # 60% into 40% HP
    assert threat.moves_first is True  # Tailwind ended after turn 2


def test_protect_switch_and_speed_control_options_when_in_ko_range():
    _, checks = _checks(_LOG_ATTACKED)
    options = {o.kind: o for o in _talonflame_turn(checks, 3).decision_options}

    protect = options[DecisionKind.PROTECT]
    assert protect.move == "Protect" and protect.against == "Garchomp"
    assert "from 40% HP" in protect.summary

    switch = options[DecisionKind.SWITCH]
    assert switch.switch_to == "Amoonguss"  # brought, not on the field, not fainted
    assert switch.max_percent_taken == 20.0

    speed = options[DecisionKind.SPEED_CONTROL]
    assert speed.move == "Tailwind" and speed.against == "Garchomp"


def test_no_options_on_a_safe_turn_or_when_already_protecting():
    _, checks = _checks(_LOG_ATTACKED)
    turn1 = _talonflame_turn(checks, 1)  # full HP: 60% is not a KO
    assert turn1.incoming_threats and not turn1.incoming_threats[0].can_ko
    assert turn1.decision_options == []
    turn2 = _talonflame_turn(checks, 2)  # used Protect at 40%: no "use Protect" advice
    assert all(o.kind != DecisionKind.PROTECT for o in turn2.decision_options)


def test_best_alternatives_cover_every_opposing_target():
    _, checks = _checks(_LOG_ATTACKED)
    alternatives = _talonflame_turn(checks, 3).best_alternatives
    by_target = {a.target: a for a in alternatives}
    assert set(by_target) == {"Garchomp", "Rillaboom"}
    # The better play was the OTHER target, not a different move.
    assert alternatives[0].target == "Rillaboom" and alternatives[0].max_percent == 95.0


def test_status_moves_are_classified_by_the_engine_not_a_hardcoded_list():
    class _ProtectIsDamaging(_ScriptedCalc):
        def move_info(self, gen: int, move: str) -> MoveInfo:
            return MoveInfo(name=move, known=True, category="Physical")

    state = ShowdownReplayParser().parse(_LOG)
    checks = TurnReplaySimulator(_ProtectIsDamaging()).simulate(state, MetaContext())
    protect_turn = _talonflame_turn(checks, 2)
    # Whatever the engine says wins: here it (wrongly, on purpose) calls
    # Protect an attack, so it is treated as one — no local override list.
    assert protect_turn.note == ""
    assert {a.move for a in protect_turn.best_alternatives} >= {"Protect"}
