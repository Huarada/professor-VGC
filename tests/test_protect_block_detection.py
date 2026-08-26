"""Regression: a Protect-family block against a spread move must not vanish.

Reported gap: when a spread move (e.g. Earthquake with "[spread] p2b") was
blocked by one target's Protect, that target got no "-damage" line and simply
disappeared from the reconstructed timeline/event — losing the exact causal
signal ("this move WAS aimed at them, and they read it") a risk/reward or
predictive analysis of the turn needs. The engine also never computed what
that move would have dealt had the read been wrong.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import MetaContext
from src.services.battle_context import outcome_summary
from src.services.turn_simulator import TurnReplaySimulator

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|poke|p1|Garchomp, L50|\\n|poke|p2|Ceruledge, L50|\\n|poke|p2|Kingambit, L50|\\n"
    "|start\\n"
    "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
    "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\\n"
    "|switch|p2b: Kingambit|Kingambit, L50|100/100\\n|turn|1\\n"
    "|move|p2a: Ceruledge|Protect|p2a: Ceruledge\\n"
    "|-singleturn|p2a: Ceruledge|Protect\\n"
    "|move|p1a: Garchomp|Earthquake|p2a: Ceruledge|[spread] p2b\\n"
    "|-activate|p2a: Ceruledge|move: Protect\\n"
    "|-supereffective|p2b: Kingambit|1\\n"
    "|-damage|p2b: Kingambit|16/100\\n|win|Ash\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_blocked_target_recorded_separately_from_damaged_targets():
    state = _state()
    eq_event = next(
        e for e in state.outcome.events if e.kind == "move" and e.move == "Earthquake"
    )
    assert eq_event.blocked == ["Ceruledge"]
    assert eq_event.targets == ["Kingambit"]  # unaffected — still the real hit


def test_timeline_text_shows_the_block_causally():
    summary = outcome_summary(_state())
    assert "Ceruledge blocked (Protect)" in summary
    assert "Kingambit->16%" in summary


def test_turn_check_includes_projected_damage_for_the_blocked_target(fake_calc):
    state = _state()
    checks = TurnReplaySimulator(fake_calc).simulate(state, MetaContext())
    eq_check = next(c for c in checks if c.move == "Earthquake")
    by_target = {d.target: d for d in eq_check.damage_checks}
    assert "Kingambit" in by_target
    assert "Ceruledge" in by_target
    assert by_target["Ceruledge"].actual_result == "blocked (Protect)"
    # A real projected figure exists for the blocked target too (the fake calc
    # engine is deterministic — non-zero for any real move/species pairing).
    assert by_target["Ceruledge"].projected_max_percent > 0
