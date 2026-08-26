"""Tests for battle-outcome extraction from the Showdown log."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser

_LOG = (
    '{"formatid":"gen9vgc2026","log":"'
    "|player|p1|Ash|1|1058\\n|player|p2|Gary|2|1065\\n|clearpoke\\n"
    "|poke|p1|Torkoal, L50|\\n|poke|p2|Aerodactyl, L50|\\n|start\\n"
    "|switch|p1a: Torkoal|Torkoal, L50|100/100\\n"
    "|switch|p1b: Pyroar|Pyroar, L50|100/100\\n"
    "|switch|p2a: Aerodactyl|Aerodactyl, L50|100/100\\n|turn|1\\n"
    "|move|p2a: Aerodactyl|Rock Slide|p1a: Torkoal\\n"
    "|faint|p1b: Pyroar\\n|turn|2\\n|faint|p1a: Torkoal\\n|win|Gary\\n"
    '"}'
)


def test_outcome_extracts_winner_and_faints():
    state = ShowdownReplayParser().parse(_LOG)
    assert state.outcome is not None
    o = state.outcome
    assert o.winner_name == "Gary" and o.winner_player == "p2"
    assert o.turns == 2
    fainted = {(k.turn, k.fainted, k.player) for k in o.kos}
    assert (1, "Pyroar", "p1") in fainted
    assert (2, "Torkoal", "p1") in fainted


def test_side_of_and_rosters():
    state = ShowdownReplayParser().parse(_LOG)
    assert state.side_of()["Torkoal"] == "p1"
    assert state.side_of()["Aerodactyl"] == "p2"
    assert state.brought_by_player()["p1"] == ["Torkoal", "Pyroar"]


def test_structured_json_has_no_outcome(sample_replay_path):
    import json

    state = ShowdownReplayParser().parse(
        json.loads(sample_replay_path.read_text(encoding="utf-8"))
    )
    assert state.outcome is None
