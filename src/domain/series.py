"""Contracts for ordered BO3 review, separate from single-battle analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.exceptions import SeriesValidationError
from src.domain.models import AnalysisResult
from src.domain.bo3_inference.models import Bo3InferenceEvidence, InferenceOptions


class Bo3AnalysisRequest(BaseModel):
    """One to three replays in game order; one game is an incomplete series."""

    session_id: str
    games: list[dict[str, Any] | str]
    question: str = ""
    provider: str = "openai"
    teamsheet: dict[str, Any] | str | None = None
    inference_options: InferenceOptions = Field(default_factory=InferenceOptions)

    @field_validator("games")
    @classmethod
    def _bounded_games(cls, games: list[dict[str, Any] | str]) -> list[dict[str, Any] | str]:
        if not 1 <= len(games) <= 3:
            raise SeriesValidationError("A BO3 review needs one to three replays in game order.")
        return games


class SeriesGameEvidence(BaseModel):
    """Observed game facts; player names are stable even when p1/p2 swap."""

    model_config = ConfigDict(frozen=True)

    number: int
    winner: str | None = None
    score_before: dict[str, int]
    score_after: dict[str, int]
    players_by_side: dict[str, str]
    leads: dict[str, list[str]]
    brought: dict[str, list[str]]
    revealed: dict[str, dict[str, dict[str, list[str]]]]
    known_before: list[dict[str, Any]] = Field(default_factory=list)
    lead_changes: list[str] = Field(default_factory=list)


class Bo3Evidence(BaseModel):
    """A score is final only after two wins; absent games are never inferred."""

    model_config = ConfigDict(frozen=True)

    format_id: str
    players: list[str]
    games: list[SeriesGameEvidence]
    score: dict[str, int]
    complete: bool
    winner: str | None = None
    limitations: list[str] = Field(default_factory=list)


class Bo3AnalysisResult(BaseModel):
    """UI DTO: deterministic series evidence and independent per-game reviews."""

    session_id: str
    answer: str
    evidence: Bo3Evidence
    games: list[AnalysisResult]
    inference: Bo3InferenceEvidence | None = None
