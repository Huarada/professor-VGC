"""Immutable contracts for declarations, observations and compatible set families."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.champions import validate_champions_input
from src.domain.models import CalcRequest

StatName = Literal["hp", "atk", "def", "spa", "spd", "spe"]
STATS: tuple[StatName, ...] = ("hp", "atk", "def", "spa", "spd", "spe")


class Value(BaseModel):
    """Immutable, strict inference boundary; unknown fields cannot hide units."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldSource(Value):
    source: str
    visibility: Literal["public", "owner_private", "analyst_only"]
    available_from_game: int


class DeclaredSet(Value):
    """Only explicitly supplied fields are known; empty item means no item."""

    participant: str = Field(min_length=1)
    slot: int = Field(ge=1, le=6)
    species: str = Field(min_length=1)
    item: str | None = None
    ability: str | None = None
    moves: tuple[str, ...] = ()
    nature: str | None = None
    stat_points: dict[StatName, int] = Field(default_factory=dict)
    ivs: dict[StatName, int] = Field(default_factory=dict)
    visibility: Literal["public", "owner_private", "analyst_only"] = "public"
    available_from_game: int = Field(default=1, ge=1, le=3)
    only_game: int | None = Field(default=None, ge=1, le=3)
    source: str = "user_teamsheet"
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)

    @field_validator("nature")
    @classmethod
    def known_nature(cls, value: str | None) -> str | None:
        names = {name.casefold(): name for name in (
            "Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish", "Lax",
            "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash",
            "Calm", "Gentle", "Sassy", "Careful", "Quirky",
        )}
        if value is not None and value.casefold() not in names:
            raise ValueError("Unrecognized nature.")
        return names[value.casefold()] if value else None

    @model_validator(mode="after")
    def legal_values(self) -> DeclaredSet:
        validate_champions_input(self.model_dump())
        if any(value < 0 or value > 32 for value in self.stat_points.values()):
            raise ValueError("Champions stat points must be integers from 0 to 32.")
        if sum(self.stat_points.values()) > 66:
            raise ValueError("A Champions spread may use at most 66 stat points.")
        if any(value != 31 for value in self.ivs.values()):
            raise ValueError("Champions IVs are fixed at 31; IV inference is not applicable.")
        return self

    @field_validator("stat_points", "ivs", mode="before")
    @classmethod
    def integer_points(cls, value: object) -> object:
        if isinstance(value, dict) and any(type(point) is not int for point in value.values()):
            raise ValueError("Stat points and IVs must be integers, not EV-scale values or fractions.")
        return value


class TeamSheet(Value):
    registered_team: bool = False
    pokemon: tuple[DeclaredSet, ...] = ()

    @model_validator(mode="after")
    def unique_slots(self) -> TeamSheet:
        keys = [(entry.participant.casefold(), entry.slot, entry.visibility, entry.available_from_game,
                 entry.only_game, entry.source)
                for entry in self.pokemon]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate teamsheet slot/audience/availability declaration.")
        slots: dict[tuple[str, int], str] = {}
        species: dict[tuple[str, str], int] = {}
        for entry in self.pokemon:
            key = (entry.participant.casefold(), entry.slot)
            mon = (entry.participant.casefold(), entry.species.casefold())
            if key in slots and slots[key] != entry.species.casefold() or mon in species and species[mon] != entry.slot:
                raise ValueError("Teamsheet species and slot mappings are inconsistent.")
            slots[key] = entry.species.casefold()
            species[mon] = entry.slot
        return self


class InferenceOptions(Value):
    enabled: bool = True
    perspective: str = "public"
    hp_mode: Literal["percent", "exact"] = "percent"
    max_assignments: int = Field(default=12000, ge=1, le=100000)
    max_projections: int = Field(default=120000, ge=1, le=200000)
    max_cache_entries: int = Field(default=4096, ge=64, le=20000)
    timeout_seconds: float = Field(default=20, gt=0, le=120)


class NatureSpec(Value):
    name: str
    plus: str = ""
    minus: str = ""


class RuleProfile(Value):
    version: str
    engine_version: str
    stat_cap: Literal[32] = 32
    total_budget: Literal[66] = 66
    fixed_iv: Literal[31] = 31
    natures: tuple[NatureSpec, ...]


class HPReading(Value):
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    mode: Literal["percent", "exact"]

    @model_validator(mode="after")
    def valid_ratio(self) -> HPReading:
        if self.numerator > self.denominator:
            raise ValueError("HP cannot exceed its denominator.")
        return self


class DamageObservation(Value):
    id: str
    game: int
    event_index: int
    attacker_id: str
    defender_id: str
    category: Literal["Physical", "Special"] = "Physical"
    request: CalcRequest
    before: HPReading
    after: HPReading
    attacker_before: HPReading
    reason: str = ""


class MoveSupport(Value):
    supported: bool
    reason: str = ""
    category: str = ""


class CandidateProfile(Value):
    """Family of legal completions; unmeasured axes are not assumed to be zero."""

    pokemon_id: str
    species: str
    points: dict[StatName, int]
    natures: tuple[str, ...]


class ProjectionRequest(Value):
    calc: CalcRequest
    attacker_points: dict[StatName, int] = Field(default_factory=dict)
    defender_points: dict[StatName, int] = Field(default_factory=dict)
    attacker_nature: str = "Hardy"
    defender_nature: str = "Hardy"
    before: HPReading
    attacker_before: HPReading


class DamageWitness(Value):
    hp_before: int
    damage: int


class DamageSupport(Value):
    supported: bool
    reason: str = ""
    maximum_hp: int = 0
    witnesses: tuple[DamageWitness, ...] = ()


class PointRange(Value):
    minimum: int
    maximum: int


class CandidateSummary(Value):
    pokemon_id: str
    species: str
    stat_points: dict[StatName, PointRange]
    natures: tuple[str, ...]
    basis: Literal["declarations", "compatible_assignments", "necessary_local_bounds"]
    declared_stat_points: tuple[StatName, ...] = ()
    nature_declared: bool = False
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)
    declared_item: str | None = None
    declared_ability: str | None = None
    declared_moves: tuple[str, ...] = ()


class InferenceCheckpoint(Value):
    id: str
    game: int
    phase: Literal["before_game", "after_game"]
    perspective: str
    outcome: Literal["not_informative", "ambiguous", "identified_within_model", "conflict"]
    coverage: Literal["exhaustive", "bounded", "unsupported"]
    evaluated_assignments: int = 0
    compatible_families: int | None = None
    observation_ids: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    summaries: tuple[CandidateSummary, ...] = ()
    joint_examples: tuple[dict[str, CandidateProfile], ...] = ()
    local_constraints: tuple[str, ...] = ()
    local_evaluated_assignments: int = 0


class Bo3InferenceEvidence(Value):
    iv_policy: Literal["fixed_at_31_not_inferred"] = "fixed_at_31_not_inferred"
    units: Literal["champions_stat_points"] = "champions_stat_points"
    rule_version: str = ""
    engine_version: str = ""
    perspective: str = "public"
    continuity: Literal["registered_team", "per_game"]
    checkpoints: tuple[InferenceCheckpoint, ...] = ()
    limitations: tuple[str, ...] = ()
    projections: int = 0
    conditioned_projections: tuple[ConditionedProjection, ...] = ()


class ConditionedProjection(Value):
    """Damage under a historical event's conditions, across compatible families."""

    checkpoint_id: str
    observation_id: str
    minimum_damage: int
    maximum_damage: int
    maximum_hp_range: PointRange
    families_evaluated: int
    coverage: Literal["exhaustive", "sampled"]
