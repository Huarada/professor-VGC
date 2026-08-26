"""Typed contracts for the faithfulness benchmark.

Kept separate from ``src/domain/models.py`` on purpose — these are
benchmark-tooling types, not part of the shipped product's domain model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal[
    "move_used",
    "pokemon_played",
    "damage_range",
    "forme_change",
    "stat_stage",
    "protect_block",
    "winner",
    "forfeit",
]

Verdict = Literal["correct", "incorrect", "unverifiable"]


class AtomicClaim(BaseModel):
    """One atomic, structurally-verifiable factual claim extracted from prose.

    Every field beyond ``claim_type`` is optional — which ones matter depends
    on the type (mirrors a tagged union without needing eight separate
    Pydantic classes the judge LLM would have to pick between).
    """

    claim_type: ClaimType
    raw_text: str = ""
    subject: str = ""
    attacker: str = ""
    defender: str = ""
    move: str = ""
    min_percent: float | None = None
    max_percent: float | None = None
    forme: str = ""
    stat: str = ""
    stages: int | None = None
    blocked_move: str = ""


class ClaimVerdict(BaseModel):
    claim: AtomicClaim
    verdict: Verdict
    reason: str = ""


class ConditionResult(BaseModel):
    """One condition's (A or B) run against one fixture."""

    condition: Literal["A_grounded", "B_naive"]
    answer: str
    claims: list[ClaimVerdict] = Field(default_factory=list)

    @property
    def correct(self) -> int:
        return sum(1 for c in self.claims if c.verdict == "correct")

    @property
    def incorrect(self) -> int:
        return sum(1 for c in self.claims if c.verdict == "incorrect")

    @property
    def unverifiable(self) -> int:
        return sum(1 for c in self.claims if c.verdict == "unverifiable")

    @property
    def verifiable_total(self) -> int:
        return self.correct + self.incorrect

    @property
    def strict_rate(self) -> float | None:
        """correct / all claims (unverifiable counts against the score)."""
        total = len(self.claims)
        return (self.correct / total) if total else None

    @property
    def lenient_rate(self) -> float | None:
        """correct / verifiable claims (unverifiable excluded entirely)."""
        return (self.correct / self.verifiable_total) if self.verifiable_total else None


class FixtureResult(BaseModel):
    fixture_id: str
    tags: list[str]
    condition_a: ConditionResult
    condition_b: ConditionResult


class BenchmarkReport(BaseModel):
    provider: str
    model: str
    # Default "native" preserves the field's meaning for every report saved
    # before this field existed (run.py originally always used the native
    # pipeline for Condition A) — those older out/*.json files remain
    # correctly labeled if ever re-loaded via this model rather than read
    # as raw JSON (as damage_error_metrics.py already does).
    orchestrator: str = "native"
    fixtures: list[FixtureResult] = Field(default_factory=list)
