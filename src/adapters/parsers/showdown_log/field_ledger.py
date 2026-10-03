"""Field conditions over time: Tailwind, Trick Room, weather, terrain, screens.

Tracks both what is up RIGHT NOW (for the per-move snapshot) and the turn
windows each condition covered (for the per-turn ``FieldConditions``
summary). Two weather windows may share a turn: a weather war ends one and
opens the next mid-turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.models import FieldConditions, FieldWindow


@dataclass
class _OpenWindow:
    name: str
    start: int


class FieldLedger:
    """Running field state plus the closed turn windows."""

    def __init__(self) -> None:
        self.tailwind: dict[str, list[list[int]]] = {}
        self._tailwind_open: dict[str, int] = {}
        self.trick_room: list[list[int]] = []
        self._trick_room_open: int | None = None
        self.weather_windows: list[FieldWindow] = []
        self._weather_open: _OpenWindow | None = None
        self.terrain_windows: list[FieldWindow] = []
        self._terrain_open: _OpenWindow | None = None
        self.screen_windows: dict[str, list[FieldWindow]] = {}
        self._screens_open: dict[str, dict[str, int]] = {}
        self.room_windows: list[FieldWindow] = []  # Gravity / Magic Room / Wonder Room
        self._rooms_open: dict[str, int] = {}

    # -- current state ------------------------------------------------------ #

    @property
    def weather(self) -> str:
        return self._weather_open.name if self._weather_open else ""

    @property
    def terrain(self) -> str:
        return self._terrain_open.name if self._terrain_open else ""

    def screens(self) -> dict[str, list[str]]:
        return {p: list(open_) for p, open_ in self._screens_open.items() if open_}

    # -- Tailwind / Trick Room ------------------------------------------- #

    def start_tailwind(self, player: str, turn: int) -> None:
        self._tailwind_open.setdefault(player, turn)

    def end_tailwind(self, player: str, turn: int) -> None:
        start = self._tailwind_open.pop(player, turn)
        self.tailwind.setdefault(player, []).append([start, turn])

    def start_trick_room(self, turn: int) -> None:
        if self._trick_room_open is None:
            self._trick_room_open = turn

    def end_trick_room(self, turn: int) -> None:
        if self._trick_room_open is not None:
            self.trick_room.append([self._trick_room_open, turn])
            self._trick_room_open = None

    # -- screens ------------------------------------------------------------ #

    def start_screen(self, player: str, screen: str, turn: int) -> None:
        self._screens_open.setdefault(player, {}).setdefault(screen, turn)

    def end_screen(self, player: str, screen: str, turn: int) -> None:
        start = self._screens_open.get(player, {}).pop(screen, turn)
        self.screen_windows.setdefault(player, []).append(
            FieldWindow(name=screen, start_turn=start, end_turn=turn)
        )

    # -- rooms (Gravity, Magic Room, Wonder Room) --------------------------- #

    def start_room(self, room: str, turn: int) -> None:
        self._rooms_open.setdefault(room, turn)

    def end_room(self, room: str, turn: int) -> None:
        start = self._rooms_open.pop(room, turn)
        self.room_windows.append(FieldWindow(name=room, start_turn=start, end_turn=turn))

    # -- weather / terrain ------------------------------------------------- #

    def set_weather(self, weather: str, turn: int) -> bool:
        """Switch to ``weather`` (``""`` = none); False when nothing changed."""
        if weather == self.weather:
            return False
        if self._weather_open is not None:
            self.weather_windows.append(
                FieldWindow(
                    name=self._weather_open.name, start_turn=self._weather_open.start,
                    end_turn=turn,
                )
            )
            self._weather_open = None
        if weather:
            self._weather_open = _OpenWindow(weather, turn)
        return True

    def start_terrain(self, terrain: str, turn: int) -> None:
        self.end_terrain(turn)  # a new terrain replaces the old one
        self._terrain_open = _OpenWindow(terrain, turn)

    def end_terrain(self, turn: int) -> None:
        if self._terrain_open is not None:
            self.terrain_windows.append(
                FieldWindow(
                    name=self._terrain_open.name, start_turn=self._terrain_open.start,
                    end_turn=turn,
                )
            )
            self._terrain_open = None

    # -- result ------------------------------------------------------------ #

    def close(self, turn: int) -> None:
        """Close every window still open when the log ends."""
        for player, start in self._tailwind_open.items():
            self.tailwind.setdefault(player, []).append([start, turn])
        if self._trick_room_open is not None:
            self.trick_room.append([self._trick_room_open, turn])
        if self._weather_open is not None:
            self.weather_windows.append(
                FieldWindow(
                    name=self._weather_open.name, start_turn=self._weather_open.start,
                    end_turn=turn,
                )
            )
        self.end_terrain(turn)
        for room in list(self._rooms_open):
            self.end_room(room, turn)
        for player, open_ in self._screens_open.items():
            for name, start in open_.items():
                self.screen_windows.setdefault(player, []).append(
                    FieldWindow(name=name, start_turn=start, end_turn=turn)
                )

    def conditions(self, final_statuses: dict[str, dict[str, str]]) -> FieldConditions:
        return FieldConditions(
            tailwind=self.tailwind, trick_room=self.trick_room,
            weather=self.weather_windows, terrain=self.terrain_windows,
            screens=self.screen_windows, final_statuses=final_statuses,
        )
