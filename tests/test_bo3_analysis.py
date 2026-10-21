"""BO3 chronology, identity and state boundaries with no network or paid calls."""

import json

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.exceptions import SeriesValidationError, UnsupportedRulesError
from src.domain.models import (
    AnalysisRequest,
    AnalysisResult,
    MetaContext,
    SelectionPlan,
)
from src.domain.series import Bo3AnalysisRequest
from src.services.series_analysis import Bo3AnalysisService, prepare_series
from tests.conftest import PROMPTS, FakeLLM


def game(winner="Alice", move="Dragon Claw", swapped=False, lead="Garchomp"):
    names = ["Bob", "Alice"] if swapped else ["Alice", "Bob"]
    teams = {"Alice": ["Garchomp", "Talonflame", "Dragonite"], "Bob": ["Incineroar", "Sinistcha"]}
    lines = ["|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)"]
    for i, name in enumerate(names, 1):
        lines.append(f"|player|p{i}|{name}|")
        lines.extend(f"|poke|p{i}|{mon}, L50|" for mon in teams[name])
        chosen = [lead, "Talonflame"] if name == "Alice" else teams[name]
        lines.extend(f"|switch|p{i}{slot}: {mon}|{mon}, L50|100/100" for slot, mon in zip("ab", chosen))
    side = "p2" if swapped else "p1"
    target = "p1" if swapped else "p2"
    lines += ["|turn|1", f"|move|{side}a: {lead}|{move}|{target}a: Incineroar",
              f"|-damage|{target}a: Incineroar|70/100"]
    if winner:
        lines.append(f"|win|{winner}")
    return "\n".join(lines)


def request(*games):
    return Bo3AnalysisRequest(session_id="series", games=list(games), question="How did the players adapt?")


def prepare(*games):
    return prepare_series(ShowdownReplayParser(), request(*games))[0]


def test_two_wins_end_the_series_with_sides_swapped():
    evidence = prepare(game(), game(move="Protect", swapped=True))
    assert evidence.score == {"Alice": 2, "Bob": 0}
    assert evidence.complete and evidence.winner == "Alice"
    assert evidence.games[1].players_by_side == {"p1": "Bob", "p2": "Alice"}
    assert evidence.games[0].leads["Alice"] == ["Garchomp", "Talonflame"]


def test_third_game_decides_two_to_one_and_tracks_lead_changes():
    evidence = prepare(game(), game("Bob", "Protect"), game(move="Earthquake", lead="Dragonite"))
    assert evidence.score == {"Alice": 2, "Bob": 1}
    assert evidence.games[2].lead_changes == ["Alice"]
    assert evidence.games[2].score_before == {"Alice": 1, "Bob": 1}


def test_scouting_never_contains_later_game_reveals():
    evidence = prepare(game(), game("Bob", "Protect"), game(move="Earthquake"))
    assert evidence.games[0].known_before == []
    previous = json.dumps(evidence.games[1].known_before)
    assert "Dragon Claw" in previous and "Protect" not in previous and "Earthquake" not in previous
    assert "Protect" in json.dumps(evidence.games[2].known_before)


@pytest.mark.parametrize("games", [[game()], [game(), game("Bob", "Protect")], [game(None)]])
def test_incomplete_series_does_not_invent_a_winner(games):
    evidence = prepare(*games)
    assert not evidence.complete and evidence.winner is None
    assert evidence.limitations


@pytest.mark.parametrize("games", [
    [game(), game()],
    [game(), game(move="Protect"), game(move="Earthquake")],
    [game(None), game("Bob", "Protect")],
    [game(), game("Bob", "Protect").replace("|Bob|", "|Carol|")],
    [game(), game("Bob", "Protect").replace("Reg M-B", "Reg M-C")],
    [game(), game("Bob", "Protect").replace("|poke|p1|Dragonite", "|poke|p1|Charizard")],
])
def test_invalid_series_is_refused_before_any_model_call(games):
    with pytest.raises(SeriesValidationError):
        prepare(*games)


def test_empty_or_four_game_series_is_refused():
    for games in ([], [game()] * 4):
        with pytest.raises(SeriesValidationError):
            request(*games)


def test_series_pipeline_isolates_sessions_and_passes_only_previous_facts():
    seen = []

    class Pipeline:
        def analyze(self, req):
            seen.append(req)
            return AnalysisResult(session_id=req.session_id, question=req.question,
                                  answer="DO NOT USE GENERATED PROSE AS EVIDENCE",
                                  selection=SelectionPlan(), meta_context=MetaContext())

    llm = FakeLLM("{}", "Alice won the series 2-0.")
    service = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline,
                                llm=llm, prompts=PROMPTS)
    result = service.analyze(request(game(), game(move="Protect")))
    assert len(result.games) == 2
    assert len({req.session_id for req in seen}) == 2
    assert all(req.session_id != "series" for req in seen)
    assert seen[0].series_context["known_before"] == []
    assert "Protect" not in json.dumps(seen[1].series_context)
    assert "DO NOT USE GENERATED PROSE AS EVIDENCE" not in llm.calls[-1]["messages"][-1].content


def test_invalid_later_game_never_reaches_a_pipeline():
    def factory():
        pytest.fail("No pipeline may be built for an invalid series")

    llm = FakeLLM("{}", "unused")
    service = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=factory,
                                llm=llm, prompts=PROMPTS)
    with pytest.raises(UnsupportedRulesError):
        service.analyze(request(game(), game("Bob", "Tera Blast")))
    assert not llm.calls


@pytest.mark.parametrize("backend", ["native", "langchain", "adk"])
def test_bo3_context_reaches_all_backends_without_changing_bo1_evidence(sample_chaos_path, backend):
    from tests.test_backend_parity import _EVIDENCE_FIELDS, _adk, _langchain, _native

    analyze = {"native": _native, "langchain": _langchain, "adk": _adk}[backend]
    source = game()
    normal = analyze(sample_chaos_path, AnalysisRequest(session_id="bo1", replay_raw_text=source))
    context = {"game": 2, "known_before": [{"game": 1, "revealed": {}}]}
    series_game = analyze(sample_chaos_path, AnalysisRequest(
        session_id="bo3", replay_raw_text=source, series_context=context,
    ))
    for field in _EVIDENCE_FIELDS:
        assert getattr(series_game, field) == getattr(normal, field)
