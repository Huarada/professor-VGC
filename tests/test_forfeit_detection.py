"""Regression: a forfeit must be captured as ground truth, not silently dropped.

Reported: a game that ended by forfeit ("|-message|<name> forfeited.") was
parsed with no trace of that fact anywhere in battle_result — the explanation
AI, left to fill the gap, fabricated a "how the winner earned it" narrative
(an invented final score, "effective decision-making throughout the match")
for a game that was actually cut short, and in the process misattributed the
LOSING side's own Pokemon's damage as part of the WINNING side's strategy.
The parser now captures the forfeit explicitly so the prompt can forbid both.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.services.battle_context import outcome_summary

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|poke|p1|Garchomp, L50|\\n|poke|p2|Ceruledge, L50|\\n"
    "|start\\n"
    "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
    "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\\n|turn|1\\n"
    "|move|p1a: Garchomp|Earthquake|p2a: Ceruledge\\n"
    "|-damage|p2a: Ceruledge|40/100\\n"
    "|-message|Ash forfeited.\\n"
    "|win|Gary\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_forfeit_recorded_on_the_outcome():
    state = _state()
    assert state.outcome.forfeited_name == "Ash"
    assert state.outcome.forfeited_player == "p1"


def test_forfeit_surfaced_prominently_in_battle_result_text():
    summary = outcome_summary(_state())
    assert "IMPORTANT" in summary
    assert "Ash (p1) FORFEITED" in summary
    # The forfeit warning must appear before the winner line, not buried.
    assert summary.index("FORFEITED") < summary.index("Winner:")


def test_no_forfeit_leaves_the_field_empty():
    log = (
        '{"formatid":"gen9championsvgc2026regmb","log":"'
        "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
        "|poke|p1|Garchomp, L50|\\n|poke|p2|Ceruledge, L50|\\n"
        "|start\\n"
        "|switch|p1a: Garchomp|Garchomp, L50|100/100\\n"
        "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\\n|turn|1\\n"
        "|move|p1a: Garchomp|Earthquake|p2a: Ceruledge\\n"
        "|-damage|p2a: Ceruledge|0 fnt\\n|faint|p2a: Ceruledge\\n"
        "|win|Ash\\n"
        '"}'
    )
    state = ShowdownReplayParser().parse(log)
    assert state.outcome.forfeited_name is None
    assert state.outcome.forfeited_player is None
    assert "FORFEITED" not in outcome_summary(state)
