"""Champions received-hit counters: direct hits, causal snapshots and switch reset."""

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import MetaContext
from src.services.turn_simulator import TurnReplaySimulator
from tests.conftest import FakeCalcEngine


def source():
    return "\n".join([
        "|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)", "|player|p1|Alice|", "|player|p2|Bob|",
        "|poke|p1|Annihilape, L50|", "|poke|p1|Incineroar, L50|", "|poke|p2|Garchomp, L50|",
        "|switch|p1a: Ape|Annihilape, L50|185/185", "|switch|p2a: Chomp|Garchomp, L50|183/183",
        "|turn|1", "|move|p2a: Chomp|Dragon Claw|p1a: Ape", "|-damage|p1a: Ape|170/185",
        "|-damage|p1a: Ape|165/185|[from] brn", "|-heal|p1a: Ape|170/185|[from] item: Leftovers",
        "|move|p1a: Ape|Rage Fist|p2a: Chomp", "|-damage|p2a: Chomp|170/183",
        "|turn|2", "|move|p2a: Chomp|Triple Axel|p1a: Ape",
        "|-damage|p1a: Ape|160/185", "|-damage|p1a: Ape|150/185", "|-damage|p1a: Ape|140/185",
        "|-hitcount|p1a: Ape|3", "|move|p1a: Ape|Rage Fist|p2a: Chomp", "|-damage|p2a: Chomp|150/183",
        "|turn|3", "|switch|p1a: Incin|Incineroar, L50|170/170",
        "|turn|4", "|switch|p1a: Ape|Annihilape, L50|140/185",
        "|move|p1a: Ape|Rage Fist|p2a: Chomp", "|-damage|p2a: Chomp|140/183", "|win|Alice",
    ])


def test_champions_counter_counts_each_direct_hit_not_residuals_and_resets_on_switch():
    game = ShowdownReplayParser().parse(source())
    moves = [event for event in game.outcome.events if event.kind == "move" and event.move == "Rage Fist"]
    assert [move.state.mon("p1", "Annihilape").hits_taken for move in moves] == [1, 4, 0]
    first = next(event for event in game.outcome.events if event.kind == "move")
    assert first.state.mon("p1", "Annihilape").hits_taken == 0  # no later hit leaks backward
    again = ShowdownReplayParser().parse(source())
    assert next(event for event in again.outcome.events if event.kind == "move").state.mon("p1", "Annihilape").hits_taken == 0


def test_logged_counter_reaches_forward_turn_calculations():
    class RecordingCalc(FakeCalcEngine):
        def __init__(self):
            self.requests = []

        def calculate(self, request):
            self.requests.append(request)
            return super().calculate(request)

    engine = RecordingCalc()
    TurnReplaySimulator(engine).simulate(ShowdownReplayParser().parse(source()), MetaContext())
    counts = {request.attacker_hits_taken for request in engine.requests if request.move == "Rage Fist"}
    assert {0, 1, 4}.issubset(counts)
