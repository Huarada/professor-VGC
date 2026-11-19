"""Public HTTP input contracts; internal domain DTOs do not cross this boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportInput(PublicInput):
    source: str = Field(min_length=1, max_length=8_000)


class InferenceInput(PublicInput):
    enabled: bool = True
    perspective: str = Field(default="public", max_length=120)
    hp_mode: Literal["percent", "exact"] = "percent"


class AnalysisInput(PublicInput):
    mode: Literal["bo1", "bo3"]
    provider: Literal["gemini", "openai"] = "gemini"
    orchestrator: Literal["adk", "langchain", "native"] = "adk"
    regulation: str = Field(default="auto", max_length=100)
    question: str = Field(default="", max_length=4_000)
    replay: str = Field(default="", max_length=2_000_000)
    games: list[str] = Field(default_factory=list, max_length=3)
    teamsheet: str | None = Field(default=None, max_length=200_000)
    inference: InferenceInput = Field(default_factory=InferenceInput)
    thinking_level: Literal["low", "medium"] = "low"

    @model_validator(mode="after")
    def mode_fields(self) -> AnalysisInput:
        if self.mode == "bo1" and (not self.replay.strip() or self.games or self.teamsheet):
            raise ValueError("BO1 requires one replay and no BO3 games or teamsheet.")
        if self.mode == "bo3" and (self.replay or not 1 <= len(self.games) <= 3 or
                                    any(not game.strip() for game in self.games)):
            raise ValueError("BO3 requires one to three ordered games and no BO1 replay.")
        return self
