"""Regression tests for accurate action-timeline extraction (anti-hallucination)."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser

# Pyroar faints on turn 1 to a spread Rock Slide WITHOUT ever using a move.
# Typhlosion-Hisui faints on turn 2 to Venusaur's Earth Power.
_LOG = (
    '{"formatid":"gen9vgc2026","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|poke|p1|Torkoal, L50|\\n|poke|p1|Pyroar, L50|\\n|poke|p1|Venusaur, L50|\\n"
    "|poke|p2|Aerodactyl, L50|\\n|poke|p2|Typhlosion-Hisui, L50|\\n|start\\n"
    "|switch|p1a: Torkoal|Torkoal, L50|100/100\\n"
    "|switch|p1b: Pyroar|Pyroar, L50|100/100\\n"
    "|switch|p2a: Aerodactyl|Aerodactyl, L50|100/100\\n"
    "|switch|p2b: Typhlosion|Typhlosion-Hisui, L50|100/100\\n|turn|1\\n"
    "|move|p2a: Aerodactyl|Rock Slide|p1a: Torkoal|[spread] p1a,p1b\\n"
    "|-damage|p1a: Torkoal|2/100\\n|-damage|p1b: Pyroar|0 fnt\\n|faint|p1b: Pyroar\\n"
    "|switch|p1b: Venusaur|Venusaur, L50|100/100\\n|turn|2\\n"
    "|move|p1b: Venusaur|Earth Power|p2b: Typhlosion\\n"
    "|-supereffective|p2b: Typhlosion\\n|-damage|p2b: Typhlosion|0 fnt\\n"
    "|faint|p2b: Typhlosion\\n|win|Gary\\n"
    '"}'
)


def _events(kind=None):
    st = ShowdownReplayParser().parse(_LOG)
    evs = st.outcome.events
    return [e for e in evs if kind is None or e.kind == kind]


def test_pyroar_never_acted():
    move_actors = {e.actor for e in _events("move")}
    assert "Pyroar" not in move_actors  # Pyroar fainted before acting


def test_faint_attributed_to_correct_move():
    # The move event that recorded Typhlosion fainting must be Venusaur's Earth Power.
    moves = _events("move")
    killer = next(e for e in moves if any("Typhlosion" in r and "fainted" in r for r in e.results))
    assert killer.actor == "Venusaur" and killer.move == "Earth Power"


def test_timeline_is_ordered_by_turn():
    turns = [e.turn for e in _events()]
    assert turns == sorted(turns)


def test_rock_slide_recorded_pyroar_faint():
    moves = _events("move")
    rock_slide = next(e for e in moves if e.move == "Rock Slide")
    assert any("Pyroar fainted" in r for r in rock_slide.results)
    assert "spread" in rock_slide.effects
