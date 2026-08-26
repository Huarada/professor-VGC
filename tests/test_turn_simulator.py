"""Tests for the per-turn deterministic verification loop."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import MetaContext
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import FakeCalcEngine

_LOG = (
    '{"formatid":"gen9vgc2026","log":"'
    "|player|p1|A|1|1|\\n|player|p2|B|2|1|\\n"
    "|poke|p1|Raichu, L50|\\n|poke|p2|Gholdengo, L50|\\n|start\\n"
    "|switch|p1a: Raichu|Raichu, L50|100/100\\n"
    "|switch|p2a: Gholdengo|Gholdengo, L50|100/100\\n|turn|1\\n"
    "|move|p1a: Raichu|Protect|p1a: Raichu\\n"
    "|move|p1a: Raichu|Volt Switch|p2a: Gholdengo\\n"
    "|-damage|p2a: Gholdengo|40/100\\n|win|A\\n"
    '"}'
)


def test_one_check_per_move():
    st = ShowdownReplayParser().parse(_LOG)
    checks = TurnReplaySimulator(FakeCalcEngine()).simulate(st, MetaContext())
    moves = [c.move for c in checks]
    assert moves == ["Protect", "Volt Switch"]


def test_status_move_has_no_damage_and_a_note():
    st = ShowdownReplayParser().parse(_LOG)
    checks = TurnReplaySimulator(FakeCalcEngine()).simulate(st, MetaContext())
    protect = next(c for c in checks if c.move == "Protect")
    assert not protect.damage_checks
    assert "non-damaging" in protect.note


def test_damaging_move_projects_and_records_actual():
    st = ShowdownReplayParser().parse(_LOG)
    checks = TurnReplaySimulator(FakeCalcEngine()).simulate(st, MetaContext())
    volt = next(c for c in checks if c.move == "Volt Switch")
    assert volt.damage_checks
    dc = volt.damage_checks[0]
    assert dc.target == "Gholdengo"
    assert dc.projected_max_percent > 0
    assert "40% HP" in dc.actual_result
    # ADR-029: the same fact, also as a plain number the explanation model
    # can cite verbatim instead of deriving it from the string.
    assert dc.actual_hp_remaining_percent == 40.0


_FAINT_LOG = (
    '{"formatid":"gen9vgc2026","log":"'
    "|player|p1|A|1|1|\\n|player|p2|B|2|1|\\n"
    "|poke|p1|Raichu, L50|\\n|poke|p2|Gholdengo, L50|\\n|start\\n"
    "|switch|p1a: Raichu|Raichu, L50|100/100\\n"
    "|switch|p2a: Gholdengo|Gholdengo, L50|100/100\\n|turn|1\\n"
    "|move|p1a: Raichu|Volt Switch|p2a: Gholdengo\\n"
    "|-damage|p2a: Gholdengo|0 fnt\\n|faint|p2a: Gholdengo\\n|win|A\\n"
    '"}'
)


def test_faint_records_zero_hp_remaining():
    st = ShowdownReplayParser().parse(_FAINT_LOG)
    checks = TurnReplaySimulator(FakeCalcEngine()).simulate(st, MetaContext())
    dc = checks[0].damage_checks[0]
    assert "fainted" in dc.actual_result.lower()
    assert dc.actual_hp_remaining_percent == 0.0


def test_protect_block_has_no_hp_remaining_figure(fake_calc):
    from src.domain.models import BattleEvent, BattleOutcome, GameState, PokemonSet, SideState

    state = GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp")], active=["Garchomp"]),
            SideState(player="p2", team=[PokemonSet(species="Staraptor")], active=["Staraptor"]),
        ],
        outcome=BattleOutcome(
            turns=1,
            events=[
                BattleEvent(
                    turn=1, kind="move", actor="Staraptor", actor_player="p2",
                    move="Close Combat", targets=[], blocked=["Garchomp"],
                    results=["Garchomp blocked (Protect)"],
                ),
            ],
        ),
    )
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    dc = checks[0].damage_checks[0]
    assert dc.actual_result.startswith("blocked (")
    # A block isn't a HP change at all — no number to (mis)cite here.
    assert dc.actual_hp_remaining_percent is None


def test_no_outcome_no_checks(sample_replay_path):
    import json

    st = ShowdownReplayParser().parse(json.loads(sample_replay_path.read_text("utf-8")))
    assert TurnReplaySimulator(FakeCalcEngine()).simulate(st, MetaContext()) == []
