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
                baseline_key = None
                if summary.baseline_stat_points and summary.baseline_natures and checkpoint.outcome != "conflict":
                    baseline_request = AttributeBoundsRequest(species=species,
                        stat_points=summary.baseline_stat_points, natures=summary.baseline_natures)
                    baseline_key = baseline_request.model_dump_json()
                    requests[baseline_key] = baseline_request
                entries.append((key, baseline_key))
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
        summaries = []
        for summary in checkpoint.summaries:
            envelopes = []
            for key, baseline_key in by_mon[summary.pokemon_id]:
                current = self._cache.get(key, AttributeEnvelope(species=requests[key].species,
                    supported=False, reason="Attribute projection budget reached"))
                baseline = self._cache.get(baseline_key) if baseline_key else None
                if current.supported and baseline and baseline.supported:
                    current = current.model_copy(update={"baseline_attributes": baseline.attributes})
                envelopes.append(current)
            summaries.append(summary.model_copy(update={"attributes": tuple(envelopes)}))
        return checkpoint.model_copy(update={"summaries": tuple(summaries)})
