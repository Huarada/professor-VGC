"""Tests for TurnReplaySimulator.build_protect_reads.

Reported gap: predictive explanations of Protect plays were shallow/wrong —
crediting a "prediction" to a spread move that guaranteed coverage regardless
of the block, and never flagging a Protect that denied no real threat while a
teammate died the same turn. This precomputes that classification
deterministically instead of asking the LLM to derive it from raw numbers.
"""

from __future__ import annotations

from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    CalcRequest,
    DamageResult,
    GameState,
    MetaContext,
    PokemonSet,
    SideState,
)
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import FakeCalcEngine


def test_spread_move_blocked_is_not_a_genuine_read(fake_calc):
    """Earthquake (spread) blocked by Ceruledge, also hitting Kingambit —
    guaranteed coverage regardless of the block, never a "read"."""
    log = (
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
    from src.adapters.parsers.showdown_parser import ShowdownReplayParser

    state = ShowdownReplayParser().parse(log)
    sim = TurnReplaySimulator(fake_calc)
    checks = sim.simulate(state, MetaContext())
    reads = sim.build_protect_reads(checks, state)

    assert len(reads) == 1
    read = reads[0]
    assert read.blocker == "Ceruledge"
    assert read.is_spread_move is True
    assert read.is_genuine_read is False
    assert [o.target for o in read.other_targets_hit] == ["Kingambit"]
    assert read.misallocated is False


def _direct_state(events: list[BattleEvent]) -> GameState:
    return GameState(
        sides=[
            SideState(
                player="p1",
                team=[PokemonSet(species="Charizard"), PokemonSet(species="Basculegion")],
                active=["Charizard", "Basculegion"],
            ),
            SideState(
                player="p2",
                team=[PokemonSet(species="Garchomp"), PokemonSet(species="Staraptor")],
                active=["Garchomp", "Staraptor"],
            ),
        ],
        outcome=BattleOutcome(turns=1, events=events),
    )


def test_single_target_block_with_no_other_event_is_not_misallocated(fake_calc):
    """Garchomp Protects a single-target Close Combat; nothing else happens
    that turn — a defensible read, not misallocated."""
    state = _direct_state(
        [
            BattleEvent(
                turn=2, kind="move", actor="Staraptor", actor_player="p2",
                move="Close Combat", targets=[], blocked=["Garchomp"],
                results=["Garchomp blocked (Protect)"],
            ),
        ]
    )
    sim = TurnReplaySimulator(fake_calc)
    checks = sim.simulate(state, MetaContext())
    reads = sim.build_protect_reads(checks, state)

    assert len(reads) == 1
    read = reads[0]
    assert read.is_spread_move is False
    assert read.is_genuine_read is True
    assert read.misallocated is False
    assert read.teammate_fainted == ""


def test_misallocated_protect_regression_from_real_replay(fake_calc):
    """Regression for the exact bug reported: a spread Heat Wave guaranteed-
    hits Staraptor regardless of Garchomp's block, and Staraptor faints to
    the same-turn follow-up. Garchomp's block denied no real threat (the
    fake calc's ko_chance_text never contains "OHKO") while a teammate died
    — this must be flagged misallocated, not credited as a "prediction"."""
    state = _direct_state(
        [
            BattleEvent(
                turn=5, kind="move", actor="Charizard", actor_player="p1",
                move="Heat Wave", targets=["Staraptor"], blocked=["Garchomp"],
                effects=["spread"],
                results=["Garchomp blocked (Protect)", "Staraptor->28%"],
            ),
            BattleEvent(
                turn=5, kind="move", actor="Basculegion", actor_player="p1",
                move="Last Respects", targets=["Staraptor"],
                results=["Staraptor fainted"],
            ),
        ]
    )
    sim = TurnReplaySimulator(fake_calc)
    checks = sim.simulate(state, MetaContext())
    reads = sim.build_protect_reads(checks, state)

    assert len(reads) == 1
    read = reads[0]
    assert read.blocker == "Garchomp"
    assert read.is_spread_move is True
    assert read.is_genuine_read is False
    assert read.was_immediate_ko_threat is False
    assert read.misallocated is True
    assert read.teammate_fainted == "Staraptor"


def test_mirror_species_resolves_blocker_to_the_opposing_side(fake_calc):
    """Regression: GameState.side_of() is a global species->player map, so
    when BOTH sides bring a Pokemon named "Garchomp" (a real VGC mirror
    match, and literally what the originally-reported replay had), it
    silently collapses to whichever side it saw first. A block is always
    against the opposing side's move — build_protect_reads must resolve the
    blocker to p2's Garchomp here, not misattribute it to p1's Garchomp."""
    state = GameState(
        sides=[
            SideState(
                player="p1",
                team=[PokemonSet(species="Garchomp"), PokemonSet(species="Charizard")],
                active=["Garchomp", "Charizard"],
            ),
            SideState(
                player="p2",
                team=[PokemonSet(species="Garchomp"), PokemonSet(species="Staraptor")],
                active=["Garchomp", "Staraptor"],
            ),
        ],
        outcome=BattleOutcome(
            turns=5,
            events=[
                BattleEvent(
                    turn=5, kind="move", actor="Charizard", actor_player="p1",
                    move="Heat Wave", targets=["Staraptor"], blocked=["Garchomp"],
                    effects=["spread"],
                    results=["Garchomp blocked (Protect)", "Staraptor->28%"],
                ),
                BattleEvent(
                    turn=5, kind="move", actor="Basculegion", actor_player="p1",
                    move="Last Respects", targets=["Staraptor"],
                    results=["Staraptor fainted"],
                ),
            ],
        ),
    )
    sim = TurnReplaySimulator(fake_calc)
    checks = sim.simulate(state, MetaContext())
    reads = sim.build_protect_reads(checks, state)

    assert len(reads) == 1
    read = reads[0]
    assert read.blocker_player == "p2"
    assert read.misallocated is True
    assert read.teammate_fainted == "Staraptor"


class _OhkoCalc(FakeCalcEngine):
    """Stand-in that reports every hit as a guaranteed same-turn OHKO."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        return DamageResult(
            attacker=request.attacker.species,
            defender=request.defender.species,
            move=request.move,
            damage_rolls=[200, 200],
            min_percent=100.0,
            max_percent=100.0,
            ko_chance_text="guaranteed OHKO",
            is_ko_guaranteed=True,
            description=f"{request.attacker.species} {request.move} vs {request.defender.species}",
        )


def test_justified_protect_under_ko_threat_is_never_misallocated():
    """Even if a teammate also faints the same turn, a block that denied a
    same-turn OHKO chance is justified, not misallocated."""
    state = _direct_state(
        [
            BattleEvent(
                turn=5, kind="move", actor="Charizard", actor_player="p1",
                move="Heat Wave", targets=["Staraptor"], blocked=["Garchomp"],
                effects=["spread"],
                results=["Garchomp blocked (Protect)", "Staraptor->28%"],
            ),
            BattleEvent(
                turn=5, kind="move", actor="Basculegion", actor_player="p1",
                move="Last Respects", targets=["Staraptor"],
                results=["Staraptor fainted"],
            ),
        ]
    )
    sim = TurnReplaySimulator(_OhkoCalc())
    checks = sim.simulate(state, MetaContext())
    reads = sim.build_protect_reads(checks, state)

    assert len(reads) == 1
    read = reads[0]
    assert read.was_immediate_ko_threat is True
    assert read.misallocated is False
    assert read.teammate_fainted == "Staraptor"
