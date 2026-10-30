"""Typed BO3 projection batches over the existing Champions Node worker."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.domain.bo3_inference.models import (
    AttributeBoundsRequest,
    AttributeEnvelope,
    DamageSupport,
    MoveSupport,
    ProjectionRequest,
    RuleProfile,
)
from src.domain.exceptions import CalcEngineError


class SmogonCandidateProjectionAdapter:
    """Anti-corruption layer; no inference policy or alternate damage formula."""

    def __init__(self, *, ipc: NodeIpcClient) -> None:
        self._ipc = ipc

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

    def project_batch(self, requests: Sequence[ProjectionRequest]) -> list[DamageSupport]:
        payload = []
        for entry in requests:
            request = entry.calc
            attacker = SmogonCalcAdapter._mon_payload(request.attacker)
            defender = SmogonCalcAdapter._mon_payload(request.defender)
            attacker.update({"evs": {stat: points * 8 for stat, points in entry.attacker_points.items()},
                             "nature": entry.attacker_nature})
            defender.update({"evs": {stat: points * 8 for stat, points in entry.defender_points.items()},
                             "nature": entry.defender_nature})
            payload.append({
                "attacker": attacker, "defender": defender,
                "move": request.move, "field": SmogonCalcAdapter._field_payload(request.field),
                "spreadHit": request.spread_hit, "afterFailedMove": request.after_failed_move,
                "attackerAlliesFainted": request.attacker_allies_fainted,
                "before": entry.before.model_dump(), "attackerBefore": entry.attacker_before.model_dump(),
            })
        try:
            raw = self._call("candidateBatch", requests=payload)
            if not isinstance(raw, list) or len(raw) != len(requests):
                raise CalcEngineError("Candidate batch response count mismatch")
            return [DamageSupport.model_validate(entry) for entry in raw]
        except ValidationError as exc:
            raise CalcEngineError("Malformed candidate damage support") from exc

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
