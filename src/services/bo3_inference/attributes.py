"""Read-only engine-derived outer ranges for all six unboosted attributes."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.bo3_inference.models import (
    AttributeBoundsRequest,
    AttributeEnvelope,
    InferenceCheckpoint,
)
from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import AttributeProjectionPort


class AttributeProjectionService:
    """Request-scoped cache; no sampled candidate family is treated as exhaustive."""

    def __init__(self, *, engine: AttributeProjectionPort) -> None:
        self._engine = engine
        self._cache: dict[str, AttributeEnvelope] = {}

    @property
    def projections(self) -> int:
        return len(self._cache)

    def enrich(
        self, checkpoint: InferenceCheckpoint, forms: Mapping[str, set[str]],
    ) -> InferenceCheckpoint:
        requests = {}
        by_mon = {}
        for summary in checkpoint.summaries:
            entries = []
            for species in sorted({summary.species, *forms.get(summary.pokemon_id, set())}):
                request = AttributeBoundsRequest(species=species, stat_points=summary.stat_points, natures=summary.natures)
                key = request.model_dump_json()
                requests[key] = request
                entries.append(key)
            by_mon[summary.pokemon_id] = entries
        missing = [key for key in requests if key not in self._cache]
        for start in range(0, len(missing), 64):
            keys = missing[start:start + 64]
            if len(self._cache) + len(keys) > 512:
                break
            try:
                results = self._engine.attributes_batch([requests[key] for key in keys])
                if len(results) != len(keys):
                    raise CalcEngineError("Attribute response count mismatch")
            except CalcEngineError as exc:
                results = [AttributeEnvelope(species=requests[key].species, supported=False, reason=str(exc)) for key in keys]
            self._cache.update(zip(keys, results))
        summaries = tuple(summary.model_copy(update={"attributes": tuple(
            self._cache.get(key, AttributeEnvelope(species=requests[key].species, supported=False,
                                                   reason="Attribute projection budget reached"))
            for key in by_mon[summary.pokemon_id]
        )}) for summary in checkpoint.summaries)
        return checkpoint.model_copy(update={"summaries": summaries})
