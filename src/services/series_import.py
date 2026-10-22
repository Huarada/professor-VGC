"""Acquire a BO3 once, using explicit series identity rather than player guesses."""

from __future__ import annotations

from src.domain.exceptions import ReplayFetchError, SeriesValidationError
from src.domain.interfaces import LogParser, SeriesReplayGateway
from src.domain.series import Bo3AnalysisRequest
from src.domain.series_import import ImportedReplay, ReplayLocation, SeriesImportResult
from src.services.series_analysis import prepare_series


class SeriesReplayImporter:
    """One request-scoped import; no quota use, model calls or replay uploads."""

    def __init__(self, *, gateway: SeriesReplayGateway, parser: LogParser) -> None:
        self._gateway = gateway
        self._parser = parser
        self._cache: dict[str, ImportedReplay] = {}
        self._failures: dict[str, str] = {}

    def _read(self, location: ReplayLocation) -> ImportedReplay:
        if location.id in self._cache:
            return self._cache[location.id]
        if location.id in self._failures:
            raise ReplayFetchError(self._failures[location.id])
        if len(self._cache) + len(self._failures) >= 16:
            raise ReplayFetchError("Replay discovery limit reached. Paste the missing game's replay URL.")
        try:
            replay = self._gateway.replay(location)
        except ReplayFetchError as exc:
            self._failures[location.id] = str(exc)
            raise
        self._cache[location.id] = replay
        return replay

    def import_series(self, text: str) -> SeriesImportResult:
        """Accept a room/replay URL or several whitespace-separated replay URLs."""
        tokens = text.strip().split()
        if not tokens or len(tokens) > 8:
            raise SeriesValidationError("Paste a BO3 room URL, any game replay, or up to three replay links together.")
        locations = {location.id: location for location in (self._gateway.locate(token) for token in tokens)}
        if len(locations) > 4:
            raise SeriesValidationError("Import at most one room and three games.")
        rooms = [location for location in locations.values() if location.kind == "series"]
        if len(rooms) > 1:
            raise SeriesValidationError("The pasted links identify different BO3 rooms.")
        explicit = [self._read(location) for location in locations.values() if location.kind == "replay"]
        series_ids = {r.series_id for r in explicit if r.series_id} | {room.id for room in rooms}
        if len(series_ids) > 1:
            raise SeriesValidationError("The pasted replays belong to different BO3 series.")
        series_id = next(iter(series_ids), None)
        if not series_id:
            return self._manual(explicit)
        warnings: list[str] = []
        expected: dict[int, ReplayLocation] = {}
        games: dict[int, ImportedReplay] = {}
        for replay in explicit:
            self._add(games, replay, series_id)
        room_available = False
        try:
            room_games = self._gateway.room_games(series_id)
            room_available = True
            expected = {game.number: game.location for game in room_games}
        except ReplayFetchError:
            if not explicit:
                raise ReplayFetchError("The BO3 room is unavailable or expired. Paste any uploaded game replay to find the series.") from None
            warnings.append("The live series room is unavailable; discovery uses uploaded replay metadata.")
        for number, location in expected.items():
            try:
                self._add(games, self._read(location), series_id, number)
            except ReplayFetchError:
                warnings.append(f"Game {number} could not be downloaded. Upload/share its replay in Showdown, then retry.")
        # Archived games explicitly link forward even if the series room expired.
        for _ in range(3):
            links = {game.next_id: game.number + 1 for game in games.values()
                     if game.next_id and game.number is not None and game.number < 3}
            for replay_id, number in links.items():
                expected.setdefault(number, self._gateway.locate("https://replay.pokemonshowdown.com/" + replay_id))
            pending = {replay_id: number for replay_id, number in links.items()
                       if replay_id not in self._cache and replay_id not in self._failures}
            if not pending:
                break
            for replay_id, number in pending.items():
                try:
                    self._add(games, self._read(self._gateway.locate(
                        "https://replay.pokemonshowdown.com/" + replay_id)), series_id, number)
                except ReplayFetchError:
                    warnings.append("A linked next game is unavailable. Import again after its replay is uploaded.")
        missing = set(range(1, max(set(games) | set(expected), default=1) + 1)) - games.keys()
        if explicit and (missing or not room_available and len(games) < 3):
            self._search(games, explicit[0], series_id, warnings)
        highest = max(set(games) | set(expected), default=1)
        missing = set(range(1, highest + 1)) - games.keys()
        if not games:
            raise ReplayFetchError("No uploaded games could be imported. Save/share the replays in Showdown and retry.")
        if missing:
            warnings.append("Missing game(s): " + ", ".join(map(str, sorted(missing))) + ". Game numbers have been preserved.")
        ordered = tuple(replay for _, replay in sorted(games.items()))
        if not missing:
            self._validate(ordered)
        return SeriesImportResult(games=ordered, series_id=series_id, missing_games=tuple(sorted(missing)),
                                  warnings=tuple(dict.fromkeys(warnings)))

    @staticmethod
    def _add(games: dict[int, ImportedReplay], replay: ImportedReplay, series_id: str,
             expected: int | None = None) -> None:
        if replay.series_id != series_id or replay.number is None:
            raise SeriesValidationError("A discovered replay has no matching BO3 identity. Paste the correct game links.")
        if expected is not None and replay.number != expected:
            raise SeriesValidationError("The room and replay disagree about the game number.")
        if replay.number in games and games[replay.number].location.id != replay.location.id:
            raise SeriesValidationError("Two different replays claim the same BO3 game number.")
        games[replay.number] = replay

    def _search(self, games: dict[int, ImportedReplay], seed: ImportedReplay, series_id: str,
                warnings: list[str]) -> None:
        try:
            locations = self._gateway.related(seed)
        except ReplayFetchError:
            warnings.append("Replay search is unavailable. Paste any missing game URLs together.")
            return
        for location in locations[:12]:
            try:
                replay = self._read(location)
            except ReplayFetchError:
                continue
            # Same players alone are insufficient: repeated opponents may play many sets.
            if replay.series_id == series_id:
                self._add(games, replay, series_id)
            if len(games) == 3:
                break

    def _manual(self, explicit: list[ImportedReplay]) -> SeriesImportResult:
        if not explicit or len(explicit) > 3:
            raise SeriesValidationError("A BO3 import requires one to three battle replays.")
        games = tuple(replay.model_copy(update={"number": number}) for number, replay in enumerate(explicit, 1))
        self._validate(games)
        return SeriesImportResult(games=games, warnings=(
            "These replays contain no series identifier; the supplied order was kept. Verify the game order before analysis.",))

    def _validate(self, games: tuple[ImportedReplay, ...]) -> None:
        prepare_series(self._parser, Bo3AnalysisRequest(session_id="import-validation",
                                                       games=[game.payload for game in games]))
