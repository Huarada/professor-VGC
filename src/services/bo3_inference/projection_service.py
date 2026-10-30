"""Read-only damage envelopes from evaluated joint witnesses, never from marginals."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.bo3_inference.models import (
    ConditionedProjection,
    DamageObservation,
    InferenceCheckpoint,
    PointRange,
)
from src.services.bo3_inference.solver import ProjectionBudget, projection_request


class Bo3ProjectionService:
    """Summarize historical-state projections while making sample coverage explicit."""

    @staticmethod
    def describe(
        checkpoint: InferenceCheckpoint, observations: Sequence[DamageObservation], budget: ProjectionBudget,
    ) -> list[ConditionedProjection]:
        result = []
        for observation in observations:
            supports = [budget.cache.get(projection_request(observation, assignment).model_dump_json())
                        for assignment in checkpoint.joint_examples]
            valid = [support for support in supports if support and support.supported and support.witnesses]
            if not valid:
                continue
            damage = [witness.damage for support in valid for witness in support.witnesses]
            maximum_hp = [support.maximum_hp for support in valid]
            exhaustive = checkpoint.coverage == "exhaustive" and checkpoint.compatible_families == len(valid)
            result.append(ConditionedProjection(
                checkpoint_id=checkpoint.id, observation_id=observation.id,
                minimum_damage=min(damage), maximum_damage=max(damage),
                maximum_hp_range=PointRange(minimum=min(maximum_hp), maximum=max(maximum_hp)),
                families_evaluated=len(valid), coverage="exhaustive" if exhaustive else "sampled",
            ))
        return result
