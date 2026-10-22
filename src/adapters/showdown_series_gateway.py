"""Public Showdown BO3 metadata, replay search and read-only room protocol."""

from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import requests

from src.domain.champions import require_champions_format
from src.domain.exceptions import ReplayFetchError, SeriesValidationError
from src.domain.series_import import ImportedReplay, ReplayLocation, SeriesRoomGame

_REPLAY = re.compile(r"gen9championsvgc[0-9a-z-]+-[0-9]+(?:-[a-z0-9]+)?", re.I)
_SERIES = re.compile(r"game-bestof3-gen9championsvgc[0-9a-z-]+-[0-9]+(?:-[a-z0-9]+)?", re.I)
_ORDINAL = re.compile(r"Game\s+([123])\b", re.I)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.words: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)

    def handle_data(self, data: str) -> None:
        self.words.append(data)


def _html(text: str) -> _Links:
    parsed = _Links()
    parsed.feed(text)
    return parsed


def _person(name: str) -> str:
    return "".join(c for c in name.casefold() if c.isalnum())


class ShowdownSeriesGateway:
    """Bounded acquisition; every discovered replay still needs explicit BO3 identity."""

    def __init__(self, *, timeout: float = 8, total_seconds: float = 60) -> None:
        self._timeout = timeout
        self._deadline = time.monotonic() + total_seconds

    def _remaining(self) -> float:
        remaining = min(self._timeout, self._deadline - time.monotonic())
        if remaining <= 0:
            raise ReplayFetchError("Series import timed out. Retry or paste the available replay links together.")
        return remaining

    def locate(self, url: str) -> ReplayLocation:
        try:
            parsed = urlsplit(url.strip())
            port = parsed.port
        except ValueError as exc:
            raise SeriesValidationError("Invalid Showdown URL.") from exc
        if parsed.scheme.lower() not in {"https", "http"} or parsed.username or parsed.password or port:
            raise SeriesValidationError("Use a public play.pokemonshowdown.com or replay.pokemonshowdown.com URL.")
        path = parsed.path.strip("/")
        if parsed.hostname == "play.pokemonshowdown.com" and _SERIES.fullmatch(path):
            return ReplayLocation(kind="series", id=path, url=f"https://play.pokemonshowdown.com/{path}")
        if parsed.hostname == "play.pokemonshowdown.com" and path.startswith("battle-"):
            path = path.removeprefix("battle-")
        elif parsed.hostname == "replay.pokemonshowdown.com":
            path = path.removesuffix(".json").removesuffix(".log")
        else:
            raise SeriesValidationError("Use a Champions BO3 room, battle or replay URL.")
        if not _REPLAY.fullmatch(path):
            raise SeriesValidationError("Only Pokemon Champions battle and BO3 room URLs are supported.")
        return ReplayLocation(kind="replay", id=path, url=f"https://replay.pokemonshowdown.com/{path}")

    def _get(self, url: str, *, params: dict[str, str] | None = None) -> Any:
        try:
            response = requests.get(url, params=params, timeout=self._remaining(), allow_redirects=False)
            if response.status_code != 200:
                raise ReplayFetchError(f"Showdown returned HTTP {response.status_code}; the replay may not be uploaded or public.")
            if len(response.content) > 2_000_000:
                raise ReplayFetchError("Showdown response exceeds the import size limit.")
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ReplayFetchError("Could not read Showdown replay data. Retry or paste the replay log.") from exc

    def replay(self, location: ReplayLocation) -> ImportedReplay:
        canonical = self.locate(location.url)
        if canonical.kind != "replay" or canonical.id != location.id:
            raise SeriesValidationError("A replay reference must identify a Champions battle.")
        data = self._get(canonical.url + ".json")
        if not isinstance(data, dict) or not isinstance(data.get("log"), str) or not data["log"].strip():
            raise ReplayFetchError("Showdown returned no readable battle log.")
        format_id = str(data.get("formatid", ""))
        if not format_id:
            format_id = next((line.split("|", 2)[2] for line in data["log"].splitlines() if line.startswith("|tier|")), "")
        require_champions_format(format_id)
        number, series_id, next_id = None, None, None
        for line in data["log"].splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4 or parts[1] not in {"uhtml", "uhtmlchange"}:
                continue
            parsed = _html(parts[3])
            if parts[2] == "bestof":
                ordinal = _ORDINAL.search(" ".join(parsed.words))
                for href in parsed.links:
                    if href.startswith("/") and _SERIES.fullmatch(href[1:]):
                        candidate = href[1:]
                        if series_id and (series_id != candidate or number != (int(ordinal[1]) if ordinal else None)):
                            raise SeriesValidationError("Conflicting BO3 metadata in a replay.")
                        series_id, number = candidate, int(ordinal[1]) if ordinal else None
            elif parts[2] == "next":
                for href in parsed.links:
                    if href.startswith("/battle-") and _REPLAY.fullmatch(href[8:]):
                        next_id = href[8:]
        players = data.get("players", [])
        if not isinstance(players, list) or any(not isinstance(player, str) for player in players):
            raise ReplayFetchError("Showdown returned invalid player metadata.")
        return ImportedReplay(location=canonical, payload=json.dumps(data), number=number, series_id=series_id,
                              next_id=next_id, players=tuple(str(p) for p in players), format_id=format_id)

    def _room_log(self, series_id: str) -> str:
        # Lazy import keeps BO1 and ordinary replay fetching independent of WebSockets.
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect
        from websockets.typing import Origin

        try:
            with connect("wss://sim3.psim.us/showdown/websocket", origin=Origin("https://play.pokemonshowdown.com"),
                         open_timeout=self._remaining(), close_timeout=1, max_size=2_000_000) as socket:
                socket.send("|/join " + series_id)
                for _ in range(12):
                    message = socket.recv(timeout=self._remaining())
                    if isinstance(message, str) and message.startswith(">" + series_id + "\n"):
                        if "|noinit|" in message:
                            break
                        return message
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise ReplayFetchError("The public BO3 room could not be read. Paste any uploaded game replay instead.") from exc
        raise ReplayFetchError("The BO3 room is unavailable or expired. Paste an uploaded game replay instead.")

    def room_games(self, series_id: str) -> tuple[SeriesRoomGame, ...]:
        if not _SERIES.fullmatch(series_id):
            raise SeriesValidationError("Invalid Champions BO3 room identifier.")
        games: dict[int, ReplayLocation] = {}
        for line in self._room_log(series_id).splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4 or parts[1] not in {"uhtml", "uhtmlchange"} or not re.fullmatch(r"game[123]", parts[2]):
                continue
            for href in _html(parts[3]).links:
                if href.startswith("/battle-") and _REPLAY.fullmatch(href[8:]):
                    games[int(parts[2][-1])] = self.locate("https://play.pokemonshowdown.com" + href)
        if not games:
            raise ReplayFetchError("No game links are available in this BO3 room. Paste uploaded replay links instead.")
        return tuple(SeriesRoomGame(number=number, location=location) for number, location in sorted(games.items()))

    def related(self, replay: ImportedReplay) -> tuple[ReplayLocation, ...]:
        if not replay.players or not replay.series_id:
            return ()
        data = self._get("https://replay.pokemonshowdown.com/search.json",
                         params={"user": replay.players[0], "format": replay.format_id})
        if not isinstance(data, list):
            raise ReplayFetchError("Showdown replay search returned invalid data.")
        players = {_person(name) for name in replay.players}
        found = []
        for entry in data[:50]:
            if not isinstance(entry, dict) or not isinstance(entry.get("players"), list):
                continue
            if {_person(str(p)) for p in entry["players"]} != players:
                continue
            replay_id = str(entry.get("id", ""))
            if _REPLAY.fullmatch(replay_id):
                found.append(self.locate("https://replay.pokemonshowdown.com/" + replay_id))
                if len(found) == 12:
                    break
        return tuple(found)
