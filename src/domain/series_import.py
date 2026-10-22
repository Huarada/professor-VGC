"""Immutable acquisition evidence; imported game numbers must never be renumbered."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.bo3_inference.models import TeamSheet


class ReplayLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["series", "replay"]
    id: str
    url: str


class ImportedReplay(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: ReplayLocation
    payload: str
    number: int | None = Field(default=None, ge=1, le=3)
    series_id: str | None = None
    next_id: str | None = None
    players: tuple[str, ...] = ()
    format_id: str = ""
    publicly_listed: bool = False


class SeriesRoomGame(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1, le=3)
    location: ReplayLocation


class SeriesImportResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    games: tuple[ImportedReplay, ...]
    series_id: str | None = None
    missing_games: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()
    teamsheet: TeamSheet | None = None
