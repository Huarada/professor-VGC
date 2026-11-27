"""A spread move hits the user's own partner: what happens to the partner is a synergy, never a lost action (ADR-083)."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import GameState, MetaContext
from src.services.battle_context import outcome_summary
from src.services.turn_simulator import TurnReplaySimulator
from src.services.wasted_actions import find_ally_synergies, find_wasted_actions


def _game(*turn_lines: str) -> GameState:
    """Ana: Garchomp + Rotom (a/b). Bob: Tinkaton + Manectric. Everything after turn 1 starts is ``turn_lines``."""
    lines = ["|player|p1|Ana|1|1", "|player|p2|Bob|2|1", "|poke|p1|Garchomp, L50|", "|poke|p1|Rotom-Wash, L50|",
             "|poke|p2|Tinkaton, L50|", "|poke|p2|Manectric, L50|", "|start",
             "|switch|p1a: Garchomp|Garchomp, L50|100/100", "|switch|p1b: Rotom|Rotom-Wash, L50|100/100",
             "|switch|p2a: Tinkaton|Tinkaton, L50|100/100", "|switch|p2b: Manectric|Manectric, L50|100/100",
             "|turn|1", *turn_lines, "|win|Ana"]
    return ShowdownReplayParser().parse({"formatid": "gen9championsvgc2026regmb", "log": "\n".join(lines)})


def _synergies(state: GameState) -> set[tuple[str, str, str, str]]:
    return {(s.actor_player, s.move, s.ally, s.result) for s in find_ally_synergies(state)}


def test_immune_partner_of_a_spread_move_is_a_synergy_not_a_lost_action() -> None:
    state = _game("|move|p1a: Garchomp|Earthquake|p2a: Tinkaton|[spread] p2a,p2b",
                  "|-immune|p1b: Rotom", "|-supereffective|p2a: Tinkaton|1", "|-damage|p2a: Tinkaton|30/100",
                  "|-damage|p2b: Manectric|0 fnt", "|faint|p2b: Manectric")
    assert find_wasted_actions(state) == []
    assert _synergies(state) == {("p1", "Earthquake", "Rotom-Wash", "took no damage, immune")}
    assert "Ana's Rotom-Wash [its own partner]" in outcome_summary(state, named_sides=True)


def test_ability_and_boost_triggered_in_the_partner_are_listed() -> None:
    state = _game("|move|p2a: Tinkaton|Discharge|p1a: Garchomp|[spread] p1a,p1b",
                  "|-ability|p2b: Manectric|Lightning Rod|boost", "|-boost|p2b: Manectric|spa|1",
                  "|-immune|p1a: Garchomp", "|-damage|p1b: Rotom|40/100")
    assert _synergies(state) == {("p2", "Discharge", "Manectric", "its ability Lightning Rod activated; its spa +1")}
    assert find_wasted_actions(state) == []  # it damaged an opponent, so the immune Garchomp is not a lost action


def test_a_move_that_only_fails_on_the_opponent_is_still_lost() -> None:
    state = _game("|move|p2a: Tinkaton|Fake Out|p1b: Rotom", "|-immune|p1b: Rotom")
    assert [(w.move, w.outcome, w.target_player, w.target) for w in find_wasted_actions(state)] == [
        ("Fake Out", "immune", "p1", "Rotom-Wash")]
    assert find_ally_synergies(state) == []


def test_partner_protect_against_own_spread_move_is_not_a_read() -> None:
    state = _game("|move|p1b: Rotom|Protect|p1b: Rotom", "|-singleturn|p1b: Rotom|Protect",
                  "|move|p2a: Tinkaton|Protect|p2a: Tinkaton", "|-singleturn|p2a: Tinkaton|Protect",
                  "|move|p1a: Garchomp|Earthquake|p2a: Tinkaton|[spread] p2b",
                  "|-activate|p1b: Rotom|move: Protect", "|-activate|p2a: Tinkaton|move: Protect",
                  "|-damage|p2b: Manectric|20/100")
    assert _synergies(state) == {("p1", "Earthquake", "Rotom-Wash", "avoided it with Protect")}
    assert find_wasted_actions(state) == []
    from tests.conftest import FakeCalcEngine

    simulator = TurnReplaySimulator(FakeCalcEngine())
    checks = simulator.simulate(state, MetaContext())
    reads = simulator.build_protect_reads(checks, state)
    assert [(r.blocker, r.blocker_player, r.attacker_player) for r in reads] == [("Tinkaton", "p2", "p1")]
    assert all(d.target_player != "p1" for r in reads for d in r.other_targets_hit)


def test_later_lines_are_not_credited_to_the_last_move() -> None:
    """A switch ends the move: the switch-in's Intimidate, end-of-turn heals and item ticks are not partner effects."""
    state = _game("|move|p1a: Garchomp|Dragon Claw|p2a: Tinkaton", "|-damage|p2a: Tinkaton|0 fnt", "|faint|p2a: Tinkaton",
                  "|switch|p1b: Rotom|Rotom-Wash, L50|90/100", "|-ability|p1b: Rotom|Levitate", "|",
                  "|-heal|p1b: Rotom|95/100|[from] ability: Rain Dish", "|upkeep")
    assert find_ally_synergies(state) == []
    item = _game("|move|p1a: Garchomp|Dragon Claw|p2a: Tinkaton", "|-damage|p2a: Tinkaton|50/100",
                 "|-heal|p1b: Rotom|95/100|[from] item: Leftovers")
    assert find_ally_synergies(item) == []


def test_healing_the_partner_is_a_synergy_even_if_it_does_nothing_to_the_opponent() -> None:
    state = _game("|move|p1a: Garchomp|Life Dew|p1a: Garchomp", "|-heal|p1b: Rotom|80/100", "|-fail|p1a: Garchomp|heal")
    assert _synergies(state) == {("p1", "Life Dew", "Rotom-Wash", "was healed")}
    assert find_wasted_actions(state) == []
