"""Immutable catalog of factual BO3 claims approved before model narration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    checkpoint_id: str
    game: int = Field(ge=1, le=3)
    pokemon_id: str
    field: str
    basis: Literal["checkpoint_result", "checkpoint_declaration", "engine_form_ability"]


class NarrativeClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    pokemon_id: str
    species: str
    kind: Literal["nature", "item", "base_ability", "moves", "forme_ability", "bounds", "spread_status"]
    value: str | tuple[str, ...] | tuple[int, int]
    stat: Literal["hp", "atk", "def", "spa", "spd", "spe"] | None = None
    form: str | None = None
    baseline: tuple[int, int] | None = None
    evidence: tuple[str, ...] = ()
    evidence_label: str = ""
    source: ClaimSource

    @model_validator(mode="after")
    def bound_identity(self) -> "NarrativeClaim":
        if self.source.pokemon_id != self.pokemon_id:
            raise ValueError("A claim cannot borrow another Pokemon's evidence")
        if self.kind == "bounds":
            if (self.stat is None or not isinstance(self.value, tuple) or len(self.value) != 2
                    or not all(isinstance(value, int) for value in self.value)):
                raise ValueError("A point-bound claim requires one attribute and two integer endpoints")
            low, high = self.value
            if not (isinstance(low, int) and isinstance(high, int) and 0 <= low <= high <= 32):
                raise ValueError("Champions point bounds must be within 0-32")
            if self.source.field != f"summary.stat_points.{self.stat}":
                raise ValueError("The bound reference must identify its own attribute")
            if self.baseline is not None and (len(self.baseline) != 2 or not 0 <= self.baseline[0] <= self.baseline[1] <= 32):
                raise ValueError("A bound baseline must be an ordered native point interval")
        elif self.kind == "moves":
            if not isinstance(self.value, tuple) or not all(isinstance(value, str) for value in self.value):
                raise ValueError("Moves must be a tuple of verified names")
        elif not isinstance(self.value, str):
            raise ValueError("Trait and status claims require textual values")
        if self.kind == "forme_ability" and (not self.form or self.source.basis != "engine_form_ability"):
            raise ValueError("A forme ability requires an engine-backed forme reference")
        if self.kind == "spread_status" and self.value not in {
            "known_within_supplied_model", "partially_narrowed", "original_spread_unresolved"
        }:
            raise ValueError("Unknown spread status")
        return self


class NarrativeClaimPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    game: int = Field(ge=1, le=3)
    checkpoint_id: str
    coverage: str
    claims: tuple[NarrativeClaim, ...]
    required_claim_ids: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    requires_strategy: bool = False

    @model_validator(mode="after")
    def causal_catalog(self) -> "NarrativeClaimPlan":
        ids = {claim.id for claim in self.claims}
        if len(ids) != len(self.claims) or not set(self.required_claim_ids) <= ids:
            raise ValueError("Claim IDs must be unique and all required claims must exist")
        if any(claim.source.game > self.game or claim.source.checkpoint_id != self.checkpoint_id for claim in self.claims):
            raise ValueError("A claim cannot use another checkpoint or future evidence")
        return self


class NarrativeDraft(BaseModel):
    """The model selects IDs; factual values are never accepted from model output."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_ids: tuple[str, ...]
    analysis: str = Field(max_length=30_000)
