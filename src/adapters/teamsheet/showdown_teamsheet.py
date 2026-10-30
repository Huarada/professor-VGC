"""Showdown public `showteam` packed exports; redacted stats remain unknown."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from src.domain.bo3_inference.models import DeclaredSet, FieldSource, TeamSheet
from src.domain.champions import validate_champions_input
from src.domain.exceptions import SeriesValidationError
from src.domain.names import to_id

_ROOM = re.compile(r'href=["\']/((?:game-bestof3-gen9championsvgc)[a-z0-9-]+)["\']', re.I)


class ShowdownTeamSheetParser:
    """Read the public protocol before play, never spectator chat or hidden defaults."""

    def parse(self, games: Sequence[dict[str, Any] | str]) -> TeamSheet:
        entries = []
        rooms = []
        for number, source in enumerate(games, 1):
            raw: Any = source
            if isinstance(source, str) and source.lstrip().startswith("{"):
                try:
                    raw = json.loads(source)
                except ValueError as exc:
                    raise SeriesValidationError("Cannot read public teamsheets from invalid replay JSON.") from exc
            log = raw.get("log", "") if isinstance(raw, dict) else raw
            if not isinstance(log, str):
                raise SeriesValidationError("Public teamsheets require readable battle logs.")
            players: dict[str, str] = {}
            room_ids = set()
            started = False
            seen: dict[str, str] = {}
            for line in log.splitlines():
                parts = line.split("|", 3)
                if len(parts) < 3:
                    continue
                if parts[1] == "player" and len(parts) == 4:
                    players[parts[2]] = parts[3].split("|", 1)[0]
                if parts[1] in {"switch", "turn", "move"}:
                    started = True
                if len(parts) == 4 and parts[1] in {"uhtml", "uhtmlchange"} and parts[2] == "bestof":
                    room_ids.update(_ROOM.findall(parts[3]))
                if len(parts) != 4 or parts[1] != "showteam" or started:
                    continue
                side, packed = parts[2], parts[3]
                if side not in players:
                    raise SeriesValidationError("A public teamsheet has no matching player declaration.")
                if side in seen:
                    if seen[side] != packed:
                        raise SeriesValidationError("Conflicting public teamsheets before the same game.")
                    continue
                seen[side] = packed
                entries.extend(self._unpack(players[side], packed, number))
            rooms.append(next(iter(room_ids)) if len(room_ids) == 1 else None)
        # Official BO3 rooms reuse each registered team; no inference from names/previews.
        registered = bool(rooms) and all(room == rooms[0] for room in rooms) and rooms[0] is not None
        if registered:
            known: dict[tuple[str, str, str], str] = {}
            for entry in entries:
                for field in ("item", "ability", "nature"):
                    value = getattr(entry, field)
                    if value is None:
                        continue
                    key = (to_id(entry.participant), to_id(entry.species), field)
                    if key in known and known[key] != to_id(value):
                        raise SeriesValidationError("A registered public teamsheet changed between games.")
                    known[key] = to_id(value)
        try:
            return TeamSheet(registered_team=registered, pokemon=tuple(entries))
        except ValidationError as exc:
            raise SeriesValidationError(f"Invalid public teamsheet: {exc}") from exc

    @staticmethod
    def _unpack(participant: str, packed: str, game: int) -> list[DeclaredSet]:
        members = packed.split("]")
        if not packed or len(members) > 6:
            raise SeriesValidationError("A public teamsheet must contain one to six Pokemon.")
        result = []
        for slot, member in enumerate(members, 1):
            fields = member.split("|")
            if len(fields) < 7 or not (fields[1] or fields[0]):
                raise SeriesValidationError("Malformed Showdown packed public teamsheet.")
            ability = fields[3] if fields[3] not in {"", "0", "1", "H", "S"} else None
            try:
                entry = DeclaredSet(participant=participant, slot=slot, species=fields[1] or fields[0],
                                    item=fields[2], ability=ability, moves=tuple(filter(None, fields[4].split(","))),
                                    nature=fields[5] or None, available_from_game=game, only_game=game,
                                    source=f"showdown_public_showteam_game_{game}")
                # Packed EV/IV fields are often redacted. Never interpret blanks as zero
                # investment or a guessed nature; native investment remains a search axis.
                validate_champions_input(entry.model_dump())
            except ValueError as exc:
                raise SeriesValidationError(f"Invalid Showdown public teamsheet: {exc}") from exc
            provenance = FieldSource(source=entry.source, visibility="public", available_from_game=game)
            entry = entry.model_copy(update={"field_sources": {
                field: provenance for field in ("item", "ability", "nature", "moves") if getattr(entry, field) is not None
            }})
            result.append(entry)
        return result
