"""Field conditions: Tailwind, screens, Trick Room, terrain and weather."""

from __future__ import annotations

from src.adapters.parsers.showdown_log.handlers.base import Handler, HandlerGroup
from src.adapters.parsers.showdown_log.protocol import (
    ROOMS,
    SCREENS,
    TERRAIN,
    WEATHER,
    tag_value,
    to_id,
)


class FieldHandlers(HandlerGroup):
    def routes(self) -> dict[str, Handler]:
        return {
            "-sidestart": self.on_sidestart,
            "-sideend": self.on_sideend,
            "-fieldstart": self.on_fieldstart,
            "-fieldend": self.on_fieldend,
            "-weather": self.on_weather,
        }

    def on_sidestart(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        player = self.state.roster.side_of_ref(parts[2])
        condition = parts[3].replace("move:", "").strip()
        if to_id(condition) == "tailwind":
            self.state.field.start_tailwind(player, self.state.turn)
        elif to_id(condition) in SCREENS:
            condition = SCREENS[to_id(condition)]
            self.state.field.start_screen(player, condition, self.state.turn)
        else:
            return
        self.state.timeline.emit(
            "side_start", (player, ""), f"{player}'s side: {condition} started.", [condition]
        )

    def on_sideend(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        player = self.state.roster.side_of_ref(parts[2])
        condition = parts[3].replace("move:", "").strip()
        if to_id(condition) == "tailwind":
            self.state.field.end_tailwind(player, self.state.turn)
        elif to_id(condition) in SCREENS:
            condition = SCREENS[to_id(condition)]
            self.state.field.end_screen(player, condition, self.state.turn)
        else:
            return
        self.state.timeline.emit(
            "side_end", (player, ""), f"{player}'s side: {condition} ended.", [condition]
        )

    def on_fieldstart(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        condition = parts[2].replace("move:", "").strip()
        if to_id(condition) == "trickroom":
            self.state.field.start_trick_room(self.state.turn)
            return
        if to_id(condition) in ROOMS:
            self.state.field.start_room(ROOMS[to_id(condition)], self.state.turn)
            return
        terrain = TERRAIN.get(to_id(condition))
        if terrain is None:
            return
        self.state.field.start_terrain(terrain, self.state.turn)
        self.state.timeline.emit(
            "terrain", None, f"{terrain} Terrain was set{self._source_suffix(parts)}.", [terrain]
        )

    def on_fieldend(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        condition = parts[2].replace("move:", "").strip()
        if to_id(condition) == "trickroom":
            self.state.field.end_trick_room(self.state.turn)
            return
        if to_id(condition) in ROOMS:
            self.state.field.end_room(ROOMS[to_id(condition)], self.state.turn)
            return
        terrain = TERRAIN.get(to_id(condition))
        if terrain is None or self.state.field.terrain != terrain:
            return
        self.state.field.end_terrain(self.state.turn)
        self.state.timeline.emit("terrain", None, f"{terrain} Terrain ended.", [""])

    def on_weather(self, parts: list[str]) -> None:
        if len(parts) <= 2 or any(p.strip() == "[upkeep]" for p in parts[3:]):
            return
        weather = WEATHER.get(to_id(parts[2]), "")
        if not weather and to_id(parts[2]) not in ("", "none"):
            weather = parts[2].strip()  # an id this table doesn't know yet: keep it visible
        if not self.state.field.set_weather(weather, self.state.turn):
            return
        if weather:
            text = f"Weather became {weather}{self._source_suffix(parts)}."
        else:
            text = "The weather ended."
        self.state.timeline.emit("weather", None, text, [weather])

    def _source_suffix(self, parts: list[str]) -> str:
        """`` (from p1 Torkoal's Drought)`` for an ability-set field condition."""
        source = tag_value(parts[3:], "from")
        holder = self.state.roster.resolve(tag_value(parts[3:], "of"))
        if source.lower().startswith("ability:") and holder is not None:
            return f" (from {holder[0]} {holder[1]}'s {source.split(':', 1)[1].strip()})"
        return ""
