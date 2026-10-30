"""A strict JSON teamsheet with explicit units, audience and availability."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from src.domain.bo3_inference.models import FieldSource, TeamSheet
from src.domain.champions import validate_champions_input
from src.domain.exceptions import SeriesValidationError


class JsonTeamSheetParser:
    """Normalize user JSON into declarations, never fill hidden fields."""

    def parse(self, payload: dict[str, Any] | str | None) -> TeamSheet:
        try:
            source = json.loads(payload) if isinstance(payload, str) and payload.strip() else payload
            validate_champions_input(source)
            sheet = TeamSheet.model_validate(source or {})
            entries = []
            for entry in sheet.pokemon:
                provenance = FieldSource(source=entry.source, visibility=entry.visibility,
                                         available_from_game=entry.available_from_game)
                fields = {field: provenance for field in ("item", "ability", "nature")
                          if getattr(entry, field) is not None}
                fields.update({f"stat_points.{stat}": provenance for stat in entry.stat_points})
                if entry.moves:
                    fields["moves"] = provenance
                entries.append(entry.model_copy(update={"field_sources": fields}))
            return sheet.model_copy(update={"pokemon": tuple(entries)})
        except (ValidationError, ValueError, TypeError) as exc:
            raise SeriesValidationError(f"Invalid Champions teamsheet: {exc}") from exc
