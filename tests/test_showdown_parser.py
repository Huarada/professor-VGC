"""Tests for the Showdown replay parser."""

from __future__ import annotations

import json

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.exceptions import LogParsingError


def test_parse_structured_json(sample_replay_path):
    payload = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    state = ShowdownReplayParser().parse(payload)
    assert state.format_id == "gen9championsvgc2026regmb"
    assert set(state.involved_species()) >= {"Garchomp", "Sinistcha", "Flutter Mane"}


def test_parse_json_string(sample_replay_path):
    text = sample_replay_path.read_text(encoding="utf-8")
    assert ShowdownReplayParser().parse(text).turn == 3


def test_parse_raw_log_text():
    log = (
        "|player|p1|Ash|\n|player|p2|Gary|\n"
        "|poke|p1|Garchomp, L50, M|\n|poke|p2|Sinistcha, L50|\n|turn|1\n"
        "|switch|p1a: Chomp|Garchomp, L50, M|100/100\n"
        "|move|p1a: Chomp|Earthquake|p2a: Tea\n"
    )
    state = ShowdownReplayParser().parse(log)
    assert state.turn == 1
    assert {"Garchomp", "Sinistcha"} <= set(state.involved_species())
    p1 = next(s for s in state.sides if s.player == "p1")
    garchomp = next(m for m in p1.team if m.species == "Garchomp")
    assert "Earthquake" in garchomp.moves


def test_empty_payload_raises():
    with pytest.raises(LogParsingError):
        ShowdownReplayParser().parse({"sides": []})


def test_unsupported_type_raises():
    with pytest.raises(LogParsingError):
        ShowdownReplayParser().parse(12345)  # type: ignore[arg-type]


def _real_replay_download() -> str:
    """A trimmed real Showdown replay *download* (carries battle log under 'log')."""
    return (
        '{"id":"gen9championsvgc2026regmb-1","format":"[Gen 9 Champions] VGC 2026 Reg M-B",'
        '"players":["a","b"],"formatid":"gen9championsvgc2026regmb","log":"'
        "|player|p1|a|1|1058\\n|player|p2|b|2|1065\\n|clearpoke\\n"
        "|poke|p1|Torkoal, L50, M|\\n|poke|p2|Aerodactyl, L50, M|\\n|teampreview|4\\n|start\\n"
        "|switch|p1a: Torkoal|Torkoal, L50, M|100/100\\n"
        "|switch|p2a: Aerodactyl|Aerodactyl, L50, M|100/100\\n|turn|1\\n"
        "|move|p1a: Torkoal|Eruption|p2a: Aerodactyl\\n"
        "|move|p2a: Aerodactyl|Rock Slide|p1a: Torkoal\\n|win|b\\n"
        '"}'
    )


def test_parse_full_replay_download_as_string():
    """Regression: pasting the whole replay JSON (with a 'log' field) as text."""
    state = ShowdownReplayParser().parse(_real_replay_download())
    assert state.format_id == "gen9championsvgc2026regmb"
    species = state.involved_species()
    assert "Torkoal" in species and "Aerodactyl" in species


def test_move_attributed_to_named_mon():
    state = ShowdownReplayParser().parse(_real_replay_download())
    p1 = next(s for s in state.sides if s.player == "p1")
    torkoal = next(m for m in p1.team if m.species == "Torkoal")
    assert "Eruption" in torkoal.moves


def test_parse_full_replay_download_as_dict():
    import json as _json

    state = ShowdownReplayParser().parse(_json.loads(_real_replay_download()))
    assert "Torkoal" in state.involved_species()


def test_no_battle_data_error_is_descriptive():
    import pytest as _pytest

    with _pytest.raises(LogParsingError) as excinfo:
        ShowdownReplayParser().parse('{"id": "x", "rating": 1040}')
    msg = str(excinfo.value)
    assert "no battle data" in msg.lower()
    assert "log" in msg  # names the missing field
    assert "id" in msg and "rating" in msg  # lists present top-level keys


def test_invalid_json_error_is_descriptive():
    import pytest as _pytest

    with _pytest.raises(LogParsingError) as excinfo:
        ShowdownReplayParser().parse('{"broken": ')
    assert "line" in str(excinfo.value).lower()
