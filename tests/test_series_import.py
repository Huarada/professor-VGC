"""Offline acquisition tests: official identity, chronology, failure and UX boundaries."""

import json
from unittest.mock import Mock, patch

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.showdown_series_gateway import ShowdownSeriesGateway
from src.domain.champions import validate_champions_input
from src.domain.exceptions import (
    ReplayFetchError,
    SeriesValidationError,
    UnsupportedRulesError,
)
from src.domain.series_import import SeriesRoomGame
from src.services.series_import import SeriesReplayImporter
from tests.test_bo3_analysis import game

ROOM = "game-bestof3-gen9championsvgc2026regmbbo3-100"
ROOT = "https://play.pokemonshowdown.com/" + ROOM
IDS = [f"gen9championsvgc2026regmbbo3-{number}" for number in (101, 202, 303)]
URLS = ["https://replay.pokemonshowdown.com/" + value for value in IDS]


def data(number=1, room=ROOM, *, metadata=True, next_game=True):
    log = game(winner="Bob" if number == 2 else "Alice", move=["Dragon Claw", "Protect", "Earthquake"][number - 1])
    if metadata:
        log += f'\n|uhtml|bestof|<h2><strong>Game {number}</strong> of <a href="/{room}">a best-of-3</a></h2>'
    if next_game and number < 3:
        log += f'\n|uhtml|next|Next: <a href="/battle-{IDS[number]}"><strong>Game {number + 1} of 3</strong></a>'
    return {"id": IDS[number - 1], "formatid": "gen9championsvgc2026regmbbo3",
            "format": "[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)", "players": ["Alice", "Bob"], "log": log}


class Gateway(ShowdownSeriesGateway):
    def __init__(self, *, expired=False, missing=(), unrelated=False):
        super().__init__()
        self.expired = expired
        self.missing = missing
        self.unrelated = unrelated
        self.reads = []

    def _get(self, url, *, params=None):
        if "search.json" in url:
            return [{"id": value, "players": ["Alice", "Bob"]} for value in reversed(IDS)]
        index = IDS.index(url.rsplit("/", 1)[-1].removesuffix(".json")) + 1
        self.reads.append(index)
        if index in self.missing:
            raise ReplayFetchError("Not uploaded")
        return data(index, room=ROOM + "-other" if self.unrelated and index == 1 else ROOM)

    def _room_log(self, series_id):
        if self.expired:
            raise ReplayFetchError("Room expired")
        return "\n".join(f'|uhtml|game{index}|<a href="/battle-{value}">Alice vs. Bob</a>'
                         for index, value in enumerate(IDS, 1))


def importer(gateway):
    return SeriesReplayImporter(gateway=gateway, parser=ShowdownReplayParser())


def test_one_room_url_imports_all_games_in_official_order():
    source = Gateway()
    result = importer(source).import_series(ROOT)
    assert [replay.number for replay in result.games] == [1, 2, 3]
    assert [replay.location.id for replay in result.games] == IDS
    assert result.missing_games == () and not result.warnings
    assert source.reads == [1, 2, 3]


def test_pasting_game_three_then_two_discovers_game_one_and_deduplicates():
    source = Gateway()
    result = importer(source).import_series("\n".join([URLS[2], URLS[1], URLS[2] + ".json"]))
    assert [replay.number for replay in result.games] == [1, 2, 3]
    assert sorted(source.reads) == [1, 2, 3]


def test_expired_room_finds_earlier_games_by_verified_series_metadata():
    result = importer(Gateway(expired=True)).import_series(URLS[2])
    assert [replay.number for replay in result.games] == [1, 2, 3]
    assert result.warnings and not result.missing_games


def test_same_players_in_another_set_are_never_a_series_match():
    result = importer(Gateway(expired=True, unrelated=True)).import_series(URLS[2])
    assert [replay.number for replay in result.games] == [2, 3]
    assert result.missing_games == (1,)


def test_unavailable_game_remains_in_its_original_slot():
    result = importer(Gateway(missing=(1,))).import_series(ROOT)
    assert [replay.number for replay in result.games] == [2, 3]
    assert result.missing_games == (1,)
    assert any("Game 1" in warning for warning in result.warnings)


def test_expired_room_without_any_replay_gives_actionable_error():
    with pytest.raises(ReplayFetchError, match="Paste any uploaded"):
        importer(Gateway(expired=True)).import_series(ROOT)


def test_input_with_different_rooms_is_rejected_before_room_discovery():
    with pytest.raises(SeriesValidationError, match="different BO3"):
        importer(Gateway()).import_series(ROOT + " " + ROOT + "-other")


def test_room_and_replay_number_disagreement_is_not_silently_corrected():
    source = Gateway()
    source.room_games = lambda series_id: (SeriesRoomGame(number=1, location=source.locate(URLS[1])),)
    with pytest.raises(SeriesValidationError, match="disagree"):
        importer(source).import_series(ROOT)


def test_different_replays_cannot_claim_the_same_game_number():
    source = Gateway()
    with patch.object(source, "_get", return_value=data(1)):
        with pytest.raises(SeriesValidationError, match="same BO3 game"):
            importer(source).import_series(URLS[0] + " " + URLS[1])


def test_manual_replays_without_series_metadata_keep_order_with_warning():
    source = Gateway()
    with patch.object(source, "_get", side_effect=[data(1, metadata=False, next_game=False),
                                                  data(2, metadata=False, next_game=False)]):
        result = importer(source).import_series("\n".join(URLS[:2]))
    assert result.series_id is None
    assert [replay.number for replay in result.games] == [1, 2]
    assert "supplied order" in result.warnings[0]


@pytest.mark.parametrize("url", [
    "https://example.org/" + IDS[0], "https://play.pokemonshowdown.com.evil.test/" + ROOM,
    "http://127.0.0.1/" + ROOM, "file:///etc/passwd", "https://user@play.pokemonshowdown.com/" + ROOM,
    "https://play.pokemonshowdown.com:443/" + ROOM, ROOT + "/../lobby", ROOT + "%0A|/cmd",
    "https://replay.pokemonshowdown.com/gen9ou-123",
])
def test_only_allowlisted_champions_locations_can_reach_the_network(url):
    with patch("requests.get") as get:
        with pytest.raises(SeriesValidationError):
            ShowdownSeriesGateway().locate(url)
    get.assert_not_called()


def test_room_links_are_read_only_from_numbered_protocol_entries():
    gateway = Gateway()
    with patch.object(gateway, "_room_log", return_value=(
        f'|c|Guest|<a href="/battle-{IDS[2]}">Game 1</a>\n'
        f'|uhtml|game1|<a href="/battle-{IDS[0]}">Alice vs. Bob</a>\n'
        f'|uhtmlchange|game1|<a href="/battle-{IDS[1]}">updated</a>')):
        result = gateway.room_games(ROOM)
    assert [(entry.number, entry.location.id) for entry in result] == [(1, IDS[1])]


def test_replay_chat_cannot_supply_series_identity_or_next_game():
    gateway = Gateway()
    payload = data(metadata=False, next_game=False)
    payload["log"] += f'\n|c|Guest|<h2>Game 1</h2><a href="/{ROOM}">best of 3</a>'
    with patch.object(gateway, "_get", return_value=payload):
        result = gateway.replay(gateway.locate(URLS[0]))
    assert result.series_id is result.number is result.next_id is None


@pytest.mark.parametrize("body", [None, [], {}, {"log": 7}, {"log": ""}])
def test_invalid_replay_payloads_raise_a_typed_error(body):
    gateway = Gateway()
    with patch.object(gateway, "_get", return_value=body):
        with pytest.raises(ReplayFetchError):
            gateway.replay(gateway.locate(URLS[0]))


def test_get_refuses_redirects_and_oversized_payloads():
    gateway = ShowdownSeriesGateway()
    response = Mock(status_code=302)
    with patch("requests.get", return_value=response) as get:
        with pytest.raises(ReplayFetchError, match="HTTP 302"):
            gateway.replay(gateway.locate(URLS[0]))
        assert get.call_args.kwargs["allow_redirects"] is False
    response = Mock(status_code=200, content=b"x" * 2_000_001)
    with patch("requests.get", return_value=response):
        with pytest.raises(ReplayFetchError, match="size limit"):
            gateway.replay(gateway.locate(URLS[0]))


def test_official_replay_display_format_is_accepted_without_relaxing_game_boundary():
    validate_champions_input(json.dumps(data()))
    with pytest.raises(UnsupportedRulesError):
        validate_champions_input({"format": "[Gen 9] VGC 2026 Reg M-C"})
    with pytest.raises(UnsupportedRulesError):
        validate_champions_input({**data(), "formatid": "gen9ou"})


def test_room_transport_only_joins_the_public_series_and_closes_connection():
    socket = Mock()
    socket.recv.side_effect = ["|challstr|unused", ">" + ROOM + "\n|init|battle\n|uhtml|game1|link"]
    context = Mock()
    context.__enter__ = Mock(return_value=socket)
    context.__exit__ = Mock(return_value=False)
    with patch("websockets.sync.client.connect", return_value=context) as connect:
        log = ShowdownSeriesGateway()._room_log(ROOM)
    assert "|uhtml|game1|" in log
    socket.send.assert_called_once_with("|/join " + ROOM)
    assert connect.call_args.args == ("wss://sim3.psim.us/showdown/websocket",)
    context.__exit__.assert_called_once()


def test_expired_room_protocol_and_network_timeouts_raise_typed_errors():
    context = Mock()
    socket = Mock()
    socket.recv.return_value = ">" + ROOM + "\n|noinit|nonexistent|The room does not exist."
    context.__enter__ = Mock(return_value=socket)
    context.__exit__ = Mock(return_value=False)
    with patch("websockets.sync.client.connect", return_value=context):
        with pytest.raises(ReplayFetchError, match="expired"):
            ShowdownSeriesGateway()._room_log(ROOM)
    with patch("websockets.sync.client.connect", side_effect=TimeoutError):
        with pytest.raises(ReplayFetchError, match="could not be read"):
            ShowdownSeriesGateway()._room_log(ROOM)


def test_import_deadline_fails_before_making_a_request():
    gateway = ShowdownSeriesGateway(total_seconds=-1)
    with patch("requests.get") as get:
        with pytest.raises(ReplayFetchError, match="timed out"):
            gateway.replay(gateway.locate(URLS[0]))
    get.assert_not_called()


def test_unavailable_forward_link_is_not_silently_dropped_when_room_expired():
    result = importer(Gateway(expired=True, missing=(2, 3))).import_series(URLS[0])
    assert [replay.number for replay in result.games] == [1]
    assert result.missing_games == (2,)


def test_search_does_not_fetch_different_opponents_and_caps_matching_candidates():
    gateway = ShowdownSeriesGateway()
    payload = [{"id": IDS[0], "players": ["Alice", "Stranger"]}]
    payload += [{"id": f"gen9championsvgc2026regmbbo3-{number}", "players": ["Bob", "Alice"]}
                for number in range(20)]
    with patch.object(gateway, "_get", return_value=data()):
        seed = gateway.replay(gateway.locate(URLS[0]))
    with patch.object(gateway, "_get", return_value=payload):
        candidates = gateway.related(seed)
    assert len(candidates) == 12
    assert IDS[0] not in {candidate.id for candidate in candidates}
