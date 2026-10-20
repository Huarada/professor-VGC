"""EV/nature envelope: how much can a hit vary with investment alone?

The projection assumes ONE spread per Pokemon (the most-used Chaos spread).
Real players invest differently, so before calling a projection "wrong" we
ask whether ANY EV/nature investment could have produced the observed damage.
For the exact calc request the simulator made for a hit, two extreme variants
are computed — everything else (species, item, ability, boosts, field, HP)
unchanged:

- minimum: attacker with 0 EVs and a nature lowering its attacking stat,
  defender with 252 HP / 252 defending-stat EVs and a nature raising it;
- maximum: attacker with 252 EVs in its attacking stat and a boosting
  nature, defender with 0 EVs and a nature lowering its defending stat.

A miss that falls inside this envelope is explained by spread variance; one
outside it needs something else (an unrevealed ability or item, Tera, ...).
"""

from __future__ import annotations

from scripts.faithfulness_benchmark.engine_calibration import HitKey, hit_key
from scripts.faithfulness_benchmark.observed_damage import ObservedHit
from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CalcEngineAdapter
from src.domain.models import (
    CalcRequest,
    DamageResult,
    MoveInfo,
    PokemonSet,
    SpeedComparison,
    StatSpread,
)

_RequestKey = tuple[str, str, str, float | None]  # attacker, defender, move, defender HP

# category -> (attacking stat, defending stat, attacker +nature, attacker -nature,
#              defender +nature, defender -nature)
_PROFILES = {
    "Physical": ("atk", "def", "Adamant", "Modest", "Impish", "Hasty"),
    "Special": ("spa", "spd", "Modest", "Adamant", "Careful", "Naive"),
}


class RecordingCalc:
    """Pass-through engine that remembers every damage request it served."""

    def __init__(self, engine: CalcEngineAdapter) -> None:
        self._engine = engine
        self.requests: dict[_RequestKey, CalcRequest] = {}
        # The same (attacker, defender, move, HP) is also calculated as an
        # alternative or a threat at other moments (other boosts, field, items);
        # the result description pins the exact request behind a projection.
        self._by_description: dict[tuple[_RequestKey, str], CalcRequest] = {}

    def calculate(self, request: CalcRequest) -> DamageResult:
        key = (request.attacker.species, request.defender.species, request.move,
               request.defender_hp_percent)
        self.requests.setdefault(key, request)
        result = self._engine.calculate(request)
        self._by_description.setdefault((key, result.description), request)
        return result

    def compare_speed(self, request: CalcRequest) -> SpeedComparison:
        return self._engine.compare_speed(request)

    def move_info(self, gen: int, move: str) -> MoveInfo:
        return self._engine.move_info(gen, move)

    def forme_resolves(self, gen: int, species: str) -> bool:
        return self._engine.forme_resolves(gen, species)

    def close(self) -> None:
        self._engine.close()

    def request_for(self, hit: ObservedHit, description: str | None = None) -> CalcRequest | None:
        """The request behind ``hit``'s projection: the one whose result had
        ``description`` when known, else the first one with the same key."""
        key = (hit.attacker, hit.defender, hit.move, hit.hp_before)
        if description is not None and (key, description) in self._by_description:
            return self._by_description[(key, description)]
        return self.requests.get(key)


def _with_spread(mon: PokemonSet, evs: dict[str, int], nature: str) -> PokemonSet:
    return mon.model_copy(update={"evs": StatSpread.model_validate(evs), "nature": nature})


def ev_envelope(engine: CalcEngineAdapter, request: CalcRequest) -> tuple[float, float] | None:
    """(min, max) damage percent of ``request`` across EV/nature investment."""
    try:
        category = engine.move_info(request.gen, request.move).category
    except CalcEngineError:
        return None
    profile = _PROFILES.get(category)
    if profile is None:
        return None
    offence, defence, boost, hinder, bulky, frail = profile
    weakest = request.model_copy(update={
        "attacker": _with_spread(request.attacker, {}, hinder),
        "defender": _with_spread(request.defender, {"hp": 252, defence: 252}, bulky),
    })
    strongest = request.model_copy(update={
        "attacker": _with_spread(request.attacker, {offence: 252}, boost),
        "defender": _with_spread(request.defender, {}, frail),
    })
    try:
        return engine.calculate(weakest).min_percent, engine.calculate(strongest).max_percent
    except CalcEngineError:
        return None


def envelopes_for(
    hits: list[ObservedHit],
    recorder: RecordingCalc,
    engine: CalcEngineAdapter,
    descriptions: dict[HitKey, str] | None = None,
) -> dict[HitKey, tuple[float, float]]:
    """EV/nature envelope for every clean observed hit whose request was recorded.

    ``descriptions`` (from ``projection_descriptions``) pins each hit to the
    exact request its projection came from."""
    envelopes: dict[HitKey, tuple[float, float]] = {}
    for hit in hits:
        request = recorder.request_for(hit, (descriptions or {}).get(hit_key(hit)))
        if request is None or not hit.projectable:
            continue
        envelope = ev_envelope(engine, request)
        if envelope is not None:
            envelopes[hit_key(hit)] = envelope
    return envelopes
