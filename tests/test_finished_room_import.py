"""Read-only recovery of a two-game BO3 whose replays were never published."""

from unittest.mock import Mock, patch

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.showdown_series_gateway import ShowdownSeriesGateway
from src.domain.exceptions import ReplayFetchError, ReplayNotFoundError
from src.domain.series import Bo3AnalysisRequest
from src.services.series_analysis import prepare_series
from src.services.series_import import SeriesReplayImporter
from tests.test_series_import import IDS, ROOM, ROOT, URLS, data


class FinishedRooms(ShowdownSeriesGateway):
    def __init__(self, *, unfinished=False, expired=False):
        super().__init__()
        self.unfinished = unfinished
        self.expired = expired
        self.rooms = []

    def _get(self, url, *, params=None):
        raise ReplayNotFoundError("HTTP 404")

    def _room_log(self, room_id):
        self.rooms.append(room_id)
        if room_id == ROOM:
            return "\n".join(f'|uhtml|game{i}|<a href="/battle-{key}">Game {i}</a>'
                             for i, key in enumerate(IDS[:2], 1))
        if self.expired:
            raise ReplayFetchError("Room expired")
        number = IDS.index(room_id.removeprefix("battle-")) + 1
        text = data(number, next_game=False)["log"].replace("|win|Bob", "|win|Alice")
        if self.unfinished and number == 2:
            text = text.replace("|win|Alice", "")
        return text + "\n|player|p1|\n|player|p2|"


def test_two_zero_import_recovers_two_finished_rooms_without_a_third_game():
    gateway = FinishedRooms()
    index = Mock()
    result = SeriesReplayImporter(gateway=gateway, parser=ShowdownReplayParser(), index=index).import_series(ROOT)
    assert len(result.games) == 2 and not result.missing_games
    assert all(game.acquisition == "public_finished_room" and not game.publicly_listed for game in result.games)
    assert all(game.source_url.startswith("https://play.pokemonshowdown.com/battle-") for game in result.games)
    assert result.games[0].players == ("Alice", "Bob")
    assert "battle-" + IDS[2] not in gateway.rooms
    assert any("Nothing was uploaded" in note for note in result.warnings)
    assert index.put.call_args.args == (ROOM, ())
    evidence, _ = prepare_series(ShowdownReplayParser(), Bo3AnalysisRequest(
        session_id="two-zero", games=[game.payload for game in result.games]))
    assert evidence.complete and evidence.score == {"Alice": 2, "Bob": 0}


def test_unfinished_second_game_is_missing_not_imported_as_completed():
    result = SeriesReplayImporter(gateway=FinishedRooms(unfinished=True), parser=ShowdownReplayParser()).import_series(ROOT)
    assert len(result.games) == 1 and result.missing_games == (2,)
    assert any("no recorded result" in note for note in result.warnings)


def test_404_and_expired_battles_explain_each_game_instead_of_blame_on_series_length():
    with pytest.raises(ReplayFetchError, match="2-0") as error:
        SeriesReplayImporter(gateway=FinishedRooms(expired=True), parser=ShowdownReplayParser()).import_series(ROOT)
    assert "404" in str(error.value) and "Game 1" in str(error.value) and "Game 2" in str(error.value)
    assert URLS[0] in str(error.value) and URLS[1] in str(error.value)


def test_http_404_uses_finished_room_fallback():
    gateway = ShowdownSeriesGateway()
    response = Mock(status_code=404)
    with patch("requests.get", return_value=response), patch.object(gateway, "_room_log", return_value=data(1)["log"]) as room:
        result = gateway.replay(gateway.locate(URLS[0]))
    room.assert_called_once_with("battle-" + IDS[0])
    assert result.acquisition == "public_finished_room"


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_other_http_failures_do_not_silently_fall_back(status):
    gateway = ShowdownSeriesGateway()
    with patch("requests.get", return_value=Mock(status_code=status)), patch.object(gateway, "_room_log") as room:
        with pytest.raises(ReplayFetchError, match=f"HTTP {status}"):
            gateway.replay(gateway.locate(URLS[0]))
    room.assert_not_called()


def test_import_receipt_links_recovered_logs_to_battle_rooms_not_404_replay_urls():
    from streamlit.testing.v1 import AppTest

    result = SeriesReplayImporter(gateway=FinishedRooms(), parser=ShowdownReplayParser()).import_series(ROOT)
    script = f'''
import streamlit as st
from src.domain.series_import import SeriesImportResult
from src.ui.series_import import render_series_import
class NoAcquisition:
    def import_bo3_replays(self, text):
        raise AssertionError("Rendering an existing receipt must not fetch or analyze")
st.session_state["bo3_import_receipt"] = SeriesImportResult.model_validate_json({result.model_dump_json()!r})
render_series_import(NoAcquisition())
'''
    app = AppTest.from_string(script, default_timeout=60).run()
    assert not app.exception
    links = [entry.value for entry in app.markdown if "Public finished battle" in entry.value]
    assert len(links) == 2
    assert all("https://play.pokemonshowdown.com/battle-" in entry for entry in links)
    assert any("2-0 BO3 needs only two games" in entry.value for entry in app.caption)
