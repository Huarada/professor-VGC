"""Typed BO3 projection batches over the existing Champions Node worker."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.domain.bo3_inference.models import (
    STATS,
    AttributeBoundsRequest,
    AttributeEnvelope,
    DamageSupport,
    MoveSupport,
    PointRange,
    ProjectionRequest,
    RuleProfile,
    TableRequest,
    TableSupport,
)
from src.domain.exceptions import CalcEngineError


class SmogonCandidateProjectionAdapter:
    """Anti-corruption layer; no inference policy or alternate damage formula."""

    def __init__(self, *, ipc: NodeIpcClient) -> None:
        self._ipc = ipc
        self._forme_abilities: dict[str, str | None] = {}
        # Engine results only (a pure function of the request), never inference state.
        self._tables: OrderedDict[str, TableSupport] = OrderedDict()

    def _call(self, command: str, **values: Any) -> Any:
        response = self._ipc.request({"cmd": command, **values})
        if not response.get("ok"):
            raise CalcEngineError(str(response.get("error", "Candidate projection failed")))
        return response.get("result")

    def rules(self) -> RuleProfile:
        try:
            return RuleProfile.model_validate(self._call("candidateRules"))
        except ValidationError as exc:
            raise CalcEngineError("Malformed Champions rule profile") from exc

    def move_support(self, move: str) -> MoveSupport:
        try:
            return MoveSupport.model_validate(self._call("candidateMove", move=move))
        except ValidationError as exc:
            raise CalcEngineError("Malformed candidate move support") from exc

    def forme_abilities(self, formes: Sequence[str]) -> dict[str, str]:
        missing = [name for name in dict.fromkeys(formes) if name not in self._forme_abilities]
        for offset in range(0, len(missing), 64):
            requests = [AttributeBoundsRequest(species=name, natures=("Hardy",),
                stat_points={stat: PointRange(minimum=0, maximum=0) for stat in STATS})
                for name in missing[offset:offset + 64]]
            for result in self.attributes_batch(requests):
                self._forme_abilities[result.species] = result.forme_ability if result.supported else None
        return {name: ability for name in formes if (ability := self._forme_abilities.get(name)) is not None}

    @staticmethod
    def _payload(entry: ProjectionRequest) -> dict[str, Any]:
        request = entry.calc
        attacker = SmogonCalcAdapter._mon_payload(request.attacker)
        defender = SmogonCalcAdapter._mon_payload(request.defender)
        attacker.update({"evs": {stat: points * 8 for stat, points in entry.attacker_points.items()},
                         "nature": entry.attacker_nature})
        defender.update({"evs": {stat: points * 8 for stat, points in entry.defender_points.items()},
                         "nature": entry.defender_nature})
        return {
            "attacker": attacker, "defender": defender,
            "move": request.move, "field": SmogonCalcAdapter._field_payload(request.field),
            "spreadHit": request.spread_hit, "afterFailedMove": request.after_failed_move,
            "criticalHit": request.critical_hit,
            "hits": request.hits,
            "attackerAlliesFainted": request.attacker_allies_fainted,
            "attackerHitsTaken": request.attacker_hits_taken,
            "before": entry.before.model_dump(), "attackerBefore": entry.attacker_before.model_dump(),
        }

    def project_batch(self, requests: Sequence[ProjectionRequest]) -> list[DamageSupport]:
        try:
            raw = self._call("candidateBatch", requests=[self._payload(entry) for entry in requests])
            if not isinstance(raw, list) or len(raw) != len(requests):
                raise CalcEngineError("Candidate batch response count mismatch")
            return [DamageSupport.model_validate(entry) for entry in raw]
        except ValidationError as exc:
            raise CalcEngineError("Malformed candidate damage support") from exc

    def project_table(self, request: TableRequest) -> TableSupport:
        key = request.model_dump_json()
        if key in self._tables:
            self._tables.move_to_end(key)
            return self._tables[key]
        try:
            result = TableSupport.model_validate(self._call("candidateTable", request={
                "base": self._payload(request.base), "axes": [axis.model_dump() for axis in request.axes],
                "after": request.after.model_dump()}))
        except ValidationError as exc:
            raise CalcEngineError("Malformed candidate table") from exc
        if len(result.cells) != 33 ** len(request.axes):
            raise CalcEngineError("Candidate table size mismatch")
        self._tables[key] = result
        if len(self._tables) > 256:
            self._tables.popitem(last=False)
        return result

    def attributes_batch(self, requests: Sequence[AttributeBoundsRequest]) -> list[AttributeEnvelope]:
        try:
            raw = self._call("candidateAttributes", requests=[entry.model_dump(mode="json") for entry in requests])
            if not isinstance(raw, list) or len(raw) != len(requests):
                raise CalcEngineError("Attribute batch response count mismatch")
            results = [AttributeEnvelope.model_validate(entry) for entry in raw]
            if any(result.species != request.species for result, request in zip(results, requests)):
                raise CalcEngineError("Attribute response species mismatch")
            return results
        except ValidationError as exc:
            raise CalcEngineError("Malformed Champions attribute bounds") from exc

    def close(self) -> None:
        self._ipc.close()
