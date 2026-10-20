"""Showdown replay parser (deterministic cleaning stage).

Turns a raw Showdown replay — either a decoded JSON object or the raw ``|``-
delimited battle log text — into a typed :class:`~src.domain.models.GameState`.
This is the *LIMPEZA PARA FILTRAR POKEMON ENVOLVIDOS (DETERMINISMO)* node in
the flow diagram: no probabilities, no LLM, just structural extraction.

The battle-log protocol itself is read by
:mod:`src.adapters.parsers.showdown_log` (rosters, the ordered action
timeline, and a per-move snapshot of the battle state); this module handles
the input shapes (replay JSON, raw text, structured team JSON), for the
analysis and for the battle panel alike.
"""

from __future__ import annotations

import json
from typing import Any

from src.adapters.parsers.showdown_log import read_log, read_replay
from src.domain.exceptions import LogParsingError
from src.domain.models import GameState, PokemonSet, SideState
from src.domain.replay_view_models import BattleReplay

# What a truncated or hand-edited replay makes the readers raise (a pydantic
# ValidationError is a ValueError). Untrusted input: reported, never a crash.
_MALFORMED = (ValueError, TypeError, KeyError, IndexError, AttributeError)


def parse_replay_for_viewer(replay: dict[str, Any] | str) -> BattleReplay:
    """The battle panel's turn-by-turn view of replay JSON or raw log text;
    empty (never raising) when the input has no usable log."""
    try:
        if isinstance(replay, str):
            text = replay.strip()
            if not text.startswith("{"):
                return read_replay(text)
            replay = json.loads(text)
        if isinstance(replay, dict):
            log = replay.get("log")
            if isinstance(log, str) and log.strip():
                return read_replay(log)
    except _MALFORMED:  # includes json.JSONDecodeError
        pass
    return BattleReplay()


class ShowdownReplayParser:
    """Concrete :class:`~src.domain.interfaces.LogParser` for Showdown."""

    def parse(self, replay: dict[str, Any] | str) -> GameState:
        """Parse structured JSON or raw log text into a GameState.

        Raises:
            LogParsingError: The input is not a usable replay, whatever its shape.
        """
        try:
            return self._parse(replay)
        except LogParsingError:
            raise
        except _MALFORMED as exc:
            raise LogParsingError(
                "The replay could not be read: it looks truncated or malformed "
                f"({type(exc).__name__}). Paste the complete replay JSON downloaded "
                "from Showdown, or the whole battle log (the lines starting with '|')."
            ) from exc

    def _parse(self, replay: dict[str, Any] | str) -> GameState:
        if isinstance(replay, str):
            text = replay.strip()
            if text.startswith("{"):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LogParsingError(
                        "Could not decode the pasted replay as JSON "
                        f"(error at line {exc.lineno}, column {exc.colno}: {exc.msg}). "
                        "Paste either the full replay JSON downloaded from Showdown "
                        "(the object containing a \"log\" field), the raw battle log "
                        "text (the lines starting with '|'), or a structured team "
                        "JSON with a \"sides\" array."
                    ) from exc
                return self._parse(decoded)
            return read_log(text)
        if isinstance(replay, dict):
            if isinstance(replay.get("log"), str) and replay["log"].strip():
                state = read_log(replay["log"])
                return state.model_copy(
                    update={
                        "format_id": replay.get("formatid") or replay.get("format")
                        or state.format_id,
                        "rating": self._coerce_rating(replay.get("rating")),
                    }
                )
            return self._parse_json(replay)
        raise LogParsingError(
            f"Unsupported replay input of type {type(replay).__name__!r}. "
            "Expected a JSON string, raw battle-log text, or a decoded dict."
        )

    # -- structured JSON ------------------------------------------------- #

    def _parse_json(self, payload: dict[str, Any]) -> GameState:
        try:
            format_id = payload.get("format") or payload.get("formatid") or "gen9vgc2025"
            turn = int(payload.get("turn", 0) or 0)
            sides_payload = payload.get("sides")
            if sides_payload is None:
                sides_payload = self._sides_from_teams(payload)
            sides = [self._parse_side(side) for side in sides_payload]
            state = GameState(
                format_id=format_id, turn=turn, sides=sides,
                rating=self._coerce_rating(payload.get("rating")),
            )
        except _MALFORMED as exc:
            raise LogParsingError(
                "Malformed replay payload: the JSON was read but its structure is "
                f"not what the parser expects ({type(exc).__name__}: {exc}). Each "
                "side needs a 'player' and a 'team' list of Pokemon objects with at "
                "least a 'species'."
            ) from exc
        if not state.sides:
            available = ", ".join(sorted(payload.keys())) or "(empty object)"
            raise LogParsingError(
                "The replay was valid JSON but contained no battle data to analyze: "
                "no 'log' field, no 'sides' array and no 'teams' object were found. "
                f"Top-level keys present: {available}. "
                "If this is a Showdown replay download, make sure you pasted the whole "
                "object including its \"log\" field; if it is a team export, wrap it as "
                '{"sides": [{"player": "p1", "team": [...]}]}.'
            )
        return state

    @staticmethod
    def _sides_from_teams(payload: dict[str, Any]) -> list[dict[str, Any]]:
        teams = payload.get("teams") or {}
        return [{"player": player, "team": team} for player, team in teams.items()]

    def _parse_side(self, side: dict[str, Any]) -> SideState:
        team = [self._parse_set(mon) for mon in side.get("team", [])]
        return SideState(
            player=str(side.get("player", "p?")),
            player_name=str(side.get("player_name", "")),
            team=team,
            active=list(side.get("active", [])),
        )

    @staticmethod
    def _parse_set(mon: dict[str, Any]) -> PokemonSet:
        return PokemonSet.model_validate(mon)

    @staticmethod
    def _coerce_rating(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
