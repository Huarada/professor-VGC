"""Tests for the standalone replay-viewer parser (UI battle panel only).

Deliberately exercises this module in isolation — it must never import or
call into ShowdownReplayParser (the LLM pipeline's parser), so these tests
don't touch that module or GameState/AnalysisResult at all.
"""

from __future__ import annotations

from src.adapters.parsers.replay_viewer_parser import parse_replay_for_viewer
from src.domain.replay_view_models import BattleReplay

_LOG = (
    '{"log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|switch|p1a: Charizard|Charizard, L50, F|100/100\\n"
    "|switch|p1b: Whimsicott|Whimsicott, L50, M|100/100\\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\\n"
    "|switch|p2b: Ceruledge|Ceruledge, L50, F|100/100\\n"
    "|turn|1\\n"
    "|detailschange|p1a: Charizard|Charizard-Mega-Y, L50, F\\n"
    "|-weather|SunnyDay\\n"
    "|move|p1a: Charizard|Overheat|p2a: Garchomp\\n"
    "|-damage|p2a: Garchomp|60/100\\n"
    "|move|p2a: Garchomp|Earthquake|p1b: Whimsicott\\n"
    "|-damage|p1b: Whimsicott|40/100\\n"
    "|-damage|p2a: Garchomp|40/100|[from] item: Life Orb\\n"
    "|-sidestart|p1: Ash|move: Tailwind\\n"
    "|turn|2\\n"
    "|-heal|p1b: Whimsicott|46/100|[from] item: Leftovers\\n"
    "|move|p2b: Ceruledge|Shadow Sneak|p1b: Whimsicott\\n"
    "|-damage|p1b: Whimsicott|0 fnt\\n"
    "|faint|p1b: Whimsicott\\n"
    "|-sideend|p1: Ash|move: Tailwind\\n"
    "|win|Gary\\n"
    '"}'
)


def _replay() -> BattleReplay:
    return parse_replay_for_viewer(_LOG)


def test_leads_snapshot():
    replay = _replay()
    leads = replay.snapshots[0]
    assert leads.turn == 0
    assert leads.active == {"p1": ["Charizard", "Whimsicott"], "p2": ["Garchomp", "Ceruledge"]}
    assert leads.pokemon["p1"]["Charizard"].hp_percent == 100.0
    assert leads.pokemon["p1"]["Charizard"].forme == ""
    assert leads.conditions == []
    assert "p1 sent out Charizard." in leads.log
    assert "p2 sent out Ceruledge." in leads.log


def test_turn_1_mega_evolution_and_residual_life_orb_damage():
    replay = _replay()
    t1 = replay.snapshots[1]
    assert t1.turn == 1
    # Mega Evolution tracked as a forme, identity (dict key) stays "Charizard".
    assert t1.pokemon["p1"]["Charizard"].forme == "Charizard-Mega-Y"
    assert t1.pokemon["p1"]["Charizard"].hp_percent == 100.0
    # Move-attributed damage.
    assert t1.pokemon["p1"]["Whimsicott"].hp_percent == 40.0
    # Garchomp: 100 -> 60 (Overheat) -> 40 (Life Orb residual, a [from] line
    # the LLM parser deliberately skips for move-attribution but this ledger
    # must still track for HP display).
    assert t1.pokemon["p2"]["Garchomp"].hp_percent == 40.0
    assert "Tailwind p1" in t1.conditions
    assert "weather SunnyDay" in t1.conditions
    assert "p1 Charizard used Overheat" in t1.log
    assert "p2 Garchomp used Earthquake" in t1.log


def test_turn_2_heal_faint_and_tailwind_window_inclusive_of_end_turn():
    replay = _replay()
    t2 = replay.snapshots[2]
    assert t2.turn == 2
    # Fainted overrides the preceding heal this same turn.
    whimsicott = t2.pokemon["p1"]["Whimsicott"]
    assert whimsicott.hp_percent == 0.0
    assert whimsicott.fainted is True
    # Garchomp's HP persists across snapshots (no new damage this turn).
    assert t2.pokemon["p2"]["Garchomp"].hp_percent == 40.0
    # Tailwind ends ON turn 2 — it was still active during turn 2 itself.
    assert "Tailwind p1" in t2.conditions
    assert "Whimsicott (p1) fainted." in t2.log


def test_winner_resolved_to_player():
    replay = _replay()
    assert replay.winner_player == "p2"


def test_mirror_species_does_not_collide_between_sides():
    """Regression: both sides bringing the same species (e.g. mirror
    Garchomp) must not let one side's HP overwrite the other's — the exact
    bug class ADR-008 fixed for the LLM pipeline's GameState.side_of(), here
    guarded independently since this module shares no code with that fix.
    """
    log = (
        '{"log":"'
        "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
        "|switch|p1a: Garchomp|Garchomp, L50, F|100/100\\n"
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100\\n"
        "|turn|1\\n"
        "|move|p1a: Garchomp|Earthquake|p2a: Garchomp\\n"
        "|-damage|p2a: Garchomp|55/100\\n"
        '"}'
    )
    replay = parse_replay_for_viewer(log)
    last = replay.snapshots[-1]
    assert last.pokemon["p1"]["Garchomp"].hp_percent == 100.0
    assert last.pokemon["p2"]["Garchomp"].hp_percent == 55.0


def test_forfeit_resolved_to_player():
    log = (
        '{"log":"'
        "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
        "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
        "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\\n"
        "|turn|1\\n"
        "|-message|Ash forfeited.\\n"
        "|win|Gary\\n"
        '"}'
    )
    replay = parse_replay_for_viewer(log)
    assert replay.forfeited_player == "p1"
    assert replay.winner_player == "p2"


def test_no_log_returns_empty_replay_without_raising():
    assert parse_replay_for_viewer({"sides": []}) == BattleReplay()
    assert parse_replay_for_viewer("not json, not a log") == BattleReplay()
    assert parse_replay_for_viewer("{not valid json") == BattleReplay()


def test_avatar_and_team_roster_captured():
    log = (
        '{"log":"'
        "|player|p1|Ash|red|1|\\n|player|p2|Gary|blue|2|\\n"
        "|poke|p1|Garchomp, L50, F|\\n|poke|p1|Charizard, L50, F|\\n"
        "|poke|p2|Ceruledge, L50, F|\\n"
        "|switch|p1a: Garchomp|Garchomp, L50, F|100/100\\n"
        "|switch|p2a: Ceruledge|Ceruledge, L50, F|100/100\\n"
        "|turn|1\\n"
        '"}'
    )
    replay = parse_replay_for_viewer(log)
    assert replay.avatars == {"p1": "red", "p2": "blue"}
    assert replay.team["p1"] == ["Garchomp", "Charizard"]  # includes the un-brought Charizard
    assert replay.team["p2"] == ["Ceruledge"]


def test_boost_tracked_and_reset_on_switch_out():
    log = (
        '{"log":"'
        "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
        "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
        "|switch|p2a: Staraptor|Staraptor, L50|100/100\\n"
        "|switch|p2b: Ceruledge|Ceruledge, L50|100/100\\n"
        "|turn|1\\n"
        "|-ability|p2a: Staraptor|Intimidate|boost\\n"
        "|-unboost|p1a: Garchomp|atk|1\\n"
        "|turn|2\\n"
        "|switch|p1a: Mawile|Mawile, L50|100/100\\n"
        "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
        '"}'
    )
    replay = parse_replay_for_viewer(log)
    t1 = replay.snapshots[1]
    assert t1.pokemon["p1"]["Garchomp"].boosts == {"atk": -1}
    # Switching out and back in resets stat stages, matching real game rules.
    t2 = replay.snapshots[2]
    assert t2.pokemon["p1"]["Garchomp"].boosts == {}
