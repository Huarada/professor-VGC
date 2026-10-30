"""Bounded durable public BO3 link index; no replay payloads or access credentials."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.domain.exceptions import ReplayFetchError
from src.domain.series_import import SeriesRoomGame

_LOCK = RLock()


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    saved_at: datetime
    games: tuple[SeriesRoomGame, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_games(self) -> _Entry:
        if len({game.number for game in self.games}) != len(self.games):
            raise ValueError("Duplicate cached game number.")
        return self


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    entries: dict[str, _Entry] = Field(default_factory=dict, max_length=64)


class JsonSeriesReplayIndex:
    """Atomic local writes, shared-process locking and fresh replay verification on use."""

    def __init__(self, *, path: Path) -> None:
        self._path = path

    def _read(self) -> _Document:
        if not self._path.exists():
            return _Document()
        if self._path.stat().st_size > 512_000:
            raise ReplayFetchError("The local BO3 link index exceeds its size limit.")
        return _Document.model_validate_json(self._path.read_text(encoding="utf-8"))

    def get(self, series_id: str) -> tuple[SeriesRoomGame, ...]:
        try:
            with _LOCK:
                entry = self._read().entries.get(series_id)
                return entry.games if entry else ()
        except (OSError, ValidationError) as exc:
            raise ReplayFetchError("The local BO3 link index could not be read.") from exc

    def put(self, series_id: str, games: tuple[SeriesRoomGame, ...]) -> None:
        if not games:
            return
        temporary: str | None = None
        try:
            with _LOCK:
                document = self._read()
                previous = document.entries.pop(series_id, None)
                combined = {game.number: game for game in previous.games} if previous else {}
                for game in games:
                    if game.number in combined and combined[game.number].location != game.location:
                        raise ReplayFetchError("Cached BO3 references disagree about a game; refusing to overwrite them.")
                    combined[game.number] = game
                document.entries[series_id] = _Entry(saved_at=datetime.now(timezone.utc),
                                                     games=tuple(combined[number] for number in sorted(combined)))
                while len(document.entries) > 64:
                    document.entries.pop(next(iter(document.entries)))
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._path.parent,
                                                 prefix="index-", suffix=".tmp", delete=False) as target:
                    temporary = target.name
                    target.write(document.model_dump_json(indent=2))
                os.replace(temporary, self._path)
                temporary = None
        except (OSError, ValidationError) as exc:
            raise ReplayFetchError("Public replay links could not be saved to the local BO3 index.") from exc
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
