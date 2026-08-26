"""Tests for resolving a pasted Showdown replay URL into replay JSON text.

The pure URL-normalization tests need no network. `fetch_replay_json`'s
error handling is tested against a mocked `requests.get` (this project's
suite never requires network/keys — see conftest.py's fakes). A final,
skippable LIVE test hits the real Showdown replay host, matching this
project's existing pattern (test_calc_engine_gametype.py) for adapters
that are worth proving against the real service when the environment
allows it — this feature was in fact built by live-verifying against a
real, user-reported replay URL before writing any UI wiring.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from src.adapters.replay_url_fetcher import fetch_replay_json, normalize_replay_json_url
from src.domain.exceptions import ReplayFetchError

_BATTLE_URL = "https://play.pokemonshowdown.com/battle-gen9championsvgc2026regmb-2661950350"
_REPLAY_JSON_URL = "https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-2661950350.json"
_REPLAY_NO_SUFFIX_URL = "https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-2661950350"


# -- normalize_replay_json_url: pure, no network ----------------------- #

def test_play_battle_url_normalizes_to_the_json_endpoint():
    assert normalize_replay_json_url(_BATTLE_URL) == _REPLAY_JSON_URL


def test_replay_url_with_json_suffix_normalizes_unchanged():
    assert normalize_replay_json_url(_REPLAY_JSON_URL) == _REPLAY_JSON_URL


def test_replay_url_without_json_suffix_gets_it_appended():
    assert normalize_replay_json_url(_REPLAY_NO_SUFFIX_URL) == _REPLAY_JSON_URL


def test_tolerates_surrounding_whitespace_and_a_trailing_slash():
    assert normalize_replay_json_url(f"  {_BATTLE_URL}/  \n") == _REPLAY_JSON_URL


def test_tolerates_uppercase_scheme_and_host():
    upper = _BATTLE_URL.replace("https://", "HTTPS://").replace(
        "play.pokemonshowdown.com", "PLAY.POKEMONSHOWDOWN.COM"
    )
    assert normalize_replay_json_url(upper) == _REPLAY_JSON_URL


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        '{"format":"gen9vgc2025","log":"|move|..."}',
        "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|",
        "https://www.google.com/search?q=pokemon",
        "https://play.pokemonshowdown.com/",  # no battle- id at all
        "not a url, just some text about gen9championsvgc2026regmb-2661950350",
    ],
)
def test_never_misidentifies_non_url_content_as_a_replay_url(text):
    assert normalize_replay_json_url(text) is None


# -- fetch_replay_json: mocked network ----------------------------------- #

def test_fetch_returns_stripped_body_on_success():
    fake_response = Mock(status_code=200, text='  {"log": "..."}  \n')
    with patch("requests.get", return_value=fake_response) as mock_get:
        result = fetch_replay_json(_REPLAY_JSON_URL)
    assert result == '{"log": "..."}'
    mock_get.assert_called_once_with(_REPLAY_JSON_URL, timeout=10.0)


def test_fetch_raises_replay_fetch_error_on_non_200():
    fake_response = Mock(status_code=404, text="")
    with patch("requests.get", return_value=fake_response):
        with pytest.raises(ReplayFetchError, match="404"):
            fetch_replay_json(_REPLAY_JSON_URL)


def test_fetch_raises_replay_fetch_error_on_empty_body():
    fake_response = Mock(status_code=200, text="   ")
    with patch("requests.get", return_value=fake_response):
        with pytest.raises(ReplayFetchError, match="empty"):
            fetch_replay_json(_REPLAY_JSON_URL)


def test_fetch_wraps_a_network_exception_never_leaks_it_raw():
    with patch("requests.get", side_effect=requests.ConnectionError("boom")):
        with pytest.raises(ReplayFetchError) as excinfo:
            fetch_replay_json(_REPLAY_JSON_URL)
    assert "boom" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, requests.ConnectionError)


# -- live, skippable integration test ------------------------------------ #

def test_fetch_the_real_reported_replay_live():
    """Reproduces the exact URL from the user's report end to end against
    the real Showdown replay host. Skips (not fails) when the network is
    unavailable — this sandbox's own local CA trust store is known-broken
    for outbound HTTPS (see ADR-015), unrelated to this feature's own
    correctness, which the mocked tests above already cover."""
    try:
        text = fetch_replay_json(_REPLAY_JSON_URL, timeout=10.0)
    except ReplayFetchError as exc:
        pytest.skip(f"Live replay host unavailable in this environment: {exc}")
    assert '"log"' in text
    assert "gen9championsvgc2026regmb" in text
