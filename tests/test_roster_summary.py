"""Regression: battle_result must state each side's roster explicitly and
authoritatively, not leave the explanation AI to infer side membership
purely by tracking every timeline event's own inline p1/p2 prefix.

Reported with a real replay and the actual generated answer: the
explanation credited two of the LOSING side's own Pokemon (Kommo-o,
Sinistcha) to the WINNING side in its synthesis paragraph. outcome_summary
now states each side's full roster once, up front, as the sole authority.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.services.battle_context import outcome_summary

_LOG = (
    '{"formatid":"gen9championsvgc2026regmb","log":"'
    "|player|p1|critical ratta|1|1|\\n|player|p2|Zyron77|2|1|\\n"
    "|poke|p1|Kommo-o, L50|\\n|poke|p1|Sinistcha, L50|\\n"
    "|poke|p2|Garchomp, L50|\\n|poke|p2|Staraptor, L50|\\n"
    "|switch|p1a: Kommo-o|Kommo-o, L50, M|100/100\\n"
    "|switch|p1b: Sinistcha|Sinistcha, L50, F|100/100\\n"
    "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\\n"
    "|switch|p2b: Staraptor|Staraptor, L50, M|100/100\\n"
    "|turn|1\\n"
    "|move|p2a: Garchomp|Rock Slide|p1a: Kommo-o\\n"
    "|-damage|p1a: Kommo-o|0 fnt\\n|faint|p1a: Kommo-o\\n"
    "|win|Zyron77\\n"
    '"}'
)


def _state():
    return ShowdownReplayParser().parse(_LOG)


def test_each_side_gets_its_own_explicit_roster_line():
    summary = outcome_summary(_state())
    assert "p1 roster (critical ratta): Kommo-o, Sinistcha." in summary
    assert "p2 roster (Zyron77): Garchomp, Staraptor." in summary


def test_losing_sides_pokemon_are_not_listed_under_the_winners_roster():
    summary = outcome_summary(_state())
    p2_line = next(line for line in summary.splitlines() if line.startswith("p2 roster"))
    assert "Kommo-o" not in p2_line
    assert "Sinistcha" not in p2_line


def test_roster_lines_appear_before_the_ordered_timeline():
    summary = outcome_summary(_state())
    assert summary.index("p1 roster") < summary.index("Ordered timeline")
    assert summary.index("p2 roster") < summary.index("Ordered timeline")
