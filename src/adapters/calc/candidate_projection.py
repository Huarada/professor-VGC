"""Typed BO3 projection batches over the existing Champions Node worker."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
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

    def __init__(self, *, ipc: NodeIpcClient, table_workers: int = 4) -> None:
        self._ipc = ipc
        self._table_worker_count = max(1, min(4, table_workers))
        self._forme_abilities: dict[str, str | None] = {}
        # Engine results only (a pure function of the request), never inference state.
        self._tables: OrderedDict[str, TableSupport] = OrderedDict()
        self._table_workers: list[NodeIpcClient] = []
        self._table_lock = Lock()

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
        with self._table_lock:
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
        with self._table_lock:
            self._tables[key] = result
            if len(self._tables) > 256:
                self._tables.popitem(last=False)
        return result

    @property
    def parallel_table_capacity(self) -> int:
        """Maximum independent table calls admitted by this adapter."""
        return self._table_worker_count

    def project_tables(self, requests: Sequence[TableRequest]) -> list[TableSupport | CalcEngineError]:
        """Evaluate independent hits on four Node workers; retain input order."""
        if not requests:
            return []
        keys = [request.model_dump_json() for request in requests]
        with self._table_lock:
            missing = {key: request for key, request in zip(keys, requests) if key not in self._tables}
            if len(missing) > 1 and not self._table_workers:
                self._table_workers = [self._ipc, *(self._ipc.fork() for _ in range(self._table_worker_count - 1))]
        if len(missing) <= 1:
            return [self._project_table_or_error(request) for request in requests]

        def evaluate(item: tuple[int, str, TableRequest]) -> tuple[str, TableSupport | CalcEngineError]:
            index, key, request = item
            worker = self._table_workers[index % len(self._table_workers)]
            try:
                raw = worker.request({"cmd": "candidateTable", "request": {
                    "base": self._payload(request.base),
                    "axes": [axis.model_dump() for axis in request.axes],
                    "after": request.after.model_dump(),
                }})
                if not raw.get("ok"):
                    raise CalcEngineError(str(raw.get("error", "Candidate projection failed")))
                result = TableSupport.model_validate(raw.get("result"))
                if len(result.cells) != 33 ** len(request.axes):
                    raise CalcEngineError("Candidate table size mismatch")
                return key, result
            except ValidationError as exc:
                return key, CalcEngineError(f"Malformed candidate table: {exc}")
            except CalcEngineError as exc:
                return key, exc

        with ThreadPoolExecutor(max_workers=min(self._table_worker_count, len(missing))) as pool:
            evaluated = dict(pool.map(evaluate, ((index, key, request)
                for index, (key, request) in enumerate(missing.items()))))
        with self._table_lock:
            for key, result in evaluated.items():
                if isinstance(result, TableSupport):
                    self._tables[key] = result
                    if len(self._tables) > 256:
                        self._tables.popitem(last=False)
            return [self._tables.get(key, evaluated.get(key, CalcEngineError("Candidate table unavailable")))
                    for key in keys]

    def _project_table_or_error(self, request: TableRequest) -> TableSupport | CalcEngineError:
        try:
            return self.project_table(request)
        except CalcEngineError as exc:
            return exc

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
        for worker in self._table_workers:
            if worker is not self._ipc:
                worker.close()
        self._ipc.close()
