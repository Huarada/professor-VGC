"""Expired BO3 rooms remain usable through verified public links, not cached logs."""

import json
from unittest.mock import patch

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.series_replay_index import JsonSeriesReplayIndex
from src.domain.exceptions import ReplayFetchError, SeriesValidationError
from src.domain.series_import import SeriesRoomGame
from src.services.series_import import SeriesReplayImporter
from tests.test_series_import import Gateway, IDS, ROOM, ROOT, URLS


class PublicGateway(Gateway):
    def _get(self, url, *, params=None):
        result = super()._get(url, params=params)
        if isinstance(result, dict):
            result.update(private=0, password=None)
        return result


def service(path, gateway):
    return SeriesReplayImporter(gateway=gateway, parser=ShowdownReplayParser(),
                                index=JsonSeriesReplayIndex(path=path))


def refs():
    gateway = Gateway()
    return tuple(SeriesRoomGame(number=number, location=gateway.locate(url))
                 for number, url in enumerate(URLS, 1))


def test_verified_seed_learns_expired_room_and_a_new_instance_downloads_fresh_games(tmp_path):
    path = tmp_path / "index.json"
    first = service(path, PublicGateway(expired=True)).import_series(URLS[2])
    assert len(first.games) == 3
    # New objects simulate an app restart: no in-memory replay cache survives.
    gateway = PublicGateway(expired=True)
    second = service(path, gateway).import_series(ROOT)
    assert [game.location.id for game in second.games] == IDS
    assert gateway.reads == [1, 2, 3]
    assert any("previously verified" in warning for warning in second.warnings)
    assert not second.missing_games
    raw = path.read_text(encoding="utf-8")
    assert '"payload"' not in raw and '"log"' not in raw and "Alice" not in raw


def test_live_room_import_is_remembered_for_later_expiry(tmp_path):
    path = tmp_path / "index.json"
    service(path, PublicGateway()).import_series(ROOT)
    result = service(path, PublicGateway(expired=True)).import_series(ROOT)
    assert len(result.games) == 3


def test_unknown_expired_room_still_requires_one_seed_and_preserves_cause(tmp_path):
    with pytest.raises(ReplayFetchError, match="Paste any uploaded") as error:
        service(tmp_path / "index.json", PublicGateway(expired=True)).import_series(ROOT)
    assert isinstance(error.value.__cause__, ReplayFetchError)


def test_private_or_unclassified_replays_are_never_written_to_shared_index(tmp_path):
    path = tmp_path / "index.json"
    # Gateway without explicit privacy metadata is unclassified, not implicitly public.
    service(path, Gateway()).import_series(ROOT)
    assert not path.exists()
    gateway = PublicGateway()
    original = gateway._get

    def private(url, *, params=None):
        result = original(url, params=params)
        if isinstance(result, dict):
            result.update(private=1, password="private-password")
        return result

    with patch.object(gateway, "_get", side_effect=private):
        service(path, gateway).import_series(ROOT)
    assert not path.exists()


def test_cached_links_do_not_resurrect_deleted_replays_or_shift_slots(tmp_path):
    path = tmp_path / "index.json"
    JsonSeriesReplayIndex(path=path).put(ROOM, refs())
    result = service(path, PublicGateway(expired=True, missing=(1,))).import_series(ROOT)
    assert result.missing_games == (1,)
    assert [game.number for game in result.games] == [2, 3]


def test_cached_replays_must_still_belong_to_the_exact_series(tmp_path):
    path = tmp_path / "index.json"
    JsonSeriesReplayIndex(path=path).put(ROOM, refs())
    with pytest.raises(SeriesValidationError, match="matching BO3 identity"):
        service(path, PublicGateway(expired=True, unrelated=True)).import_series(ROOT)


def test_cached_links_do_not_supply_replays_that_became_private(tmp_path):
    path = tmp_path / "index.json"
    JsonSeriesReplayIndex(path=path).put(ROOM, refs())
    gateway = PublicGateway(expired=True)
    original = gateway._get

    def changed(url, *, params=None):
        result = original(url, params=params)
        if isinstance(result, dict) and result.get("id") == IDS[0]:
            result["private"] = 1
        return result

    with patch.object(gateway, "_get", side_effect=changed):
        result = service(path, gateway).import_series(ROOT)
    assert result.missing_games == (1,)
    assert [game.number for game in result.games] == [2, 3]


def test_corrupt_index_does_not_block_import_from_a_valid_uploaded_seed(tmp_path):
    path = tmp_path / "index.json"
    path.write_text("{broken", encoding="utf-8")
    result = service(path, PublicGateway(expired=True)).import_series(URLS[2])
    assert len(result.games) == 3
    assert any("could not be read" in warning for warning in result.warnings)
    assert any("could not be saved" in warning for warning in result.warnings)


def test_failed_index_write_keeps_the_import_usable(tmp_path):
    path = tmp_path / "index.json"
    with patch("src.adapters.series_replay_index.os.replace", side_effect=PermissionError):
        result = service(path, PublicGateway()).import_series(ROOT)
    assert len(result.games) == 3
    assert any("could not be saved" in warning for warning in result.warnings)
    assert not list(tmp_path.glob("*.tmp"))


def test_index_is_bounded_and_merges_partial_public_links(tmp_path):
    path = tmp_path / "index.json"
    index = JsonSeriesReplayIndex(path=path)
    index.put(ROOM, refs()[:2])
    index.put(ROOM, refs()[2:])
    assert len(index.get(ROOM)) == 3
    for number in range(65):
        index.put(f"room-{number}", refs()[:1])
    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 64
    assert not index.get(ROOM)


def test_conflicting_cached_ordinals_are_never_silently_replaced(tmp_path):
    index = JsonSeriesReplayIndex(path=tmp_path / "index.json")
    index.put(ROOM, refs()[:1])
    conflicting = refs()[1].model_copy(update={"number": 1})
    with pytest.raises(ReplayFetchError, match="disagree"):
        index.put(ROOM, (conflicting,))
    assert index.get(ROOM) == refs()[:1]
