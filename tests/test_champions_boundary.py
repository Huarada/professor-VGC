"""Unsupported rules cannot enter the application, engine or generated answer."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.champions import clean_champions_knowledge, forbidden_references
from src.domain.exceptions import UnsupportedRulesError
from src.domain.models import AnalysisRequest, PokemonSet
from src.services.ground_truth import explain_champions_only, parse_replay


@pytest.mark.parametrize("text", [
    "Use Tera Steel", "Terastallize now", "teracristalização", "Z-Moves", "Z move",
    "Dynamax", "Gigantamax", "G-Max Wildfire", "Max Airstream", "Groundium Z",
    "Primal Reversion", "Tectonic Rage", "Tera Blast", "terablast", "tectonicrage", "maxairstream",
])
def test_unsupported_references_are_recognized(text):
    assert forbidden_references(text)


@pytest.mark.parametrize("text", ["Mega Evolution", "Charizard-Mega-Y", "Porygon-Z", "terrain", "Teravolt"])
def test_ordinary_names_and_mega_evolution_are_allowed(text):
    assert not forbidden_references(text)


@pytest.mark.parametrize("entry", [
    {"tera_type": "Steel"}, {"teraType": "Steel"}, {"isDynamaxed": True},
    {"moves": ["Tectonic Rage"]}, {"item": "Groundium Z"}, {"species": "Charizard-Gmax"},
])
def test_sets_cannot_silently_drop_unsupported_fields(entry):
    with pytest.raises(UnsupportedRulesError):
        PokemonSet.model_validate({"species": "Garchomp", **entry})


@pytest.mark.parametrize("line", [
    "|-terastallize|p1a: Garchomp|Steel", "|-zpower|p1a: Garchomp",
    "|-start|p1a: Charizard|Dynamax", "|-primal|p1a: Groudon",
    "|move|p1a: Garchomp|Tectonic Rage|p2a: Incineroar",
    "|tier|[Gen 9] VGC 2025 Reg H",
])
def test_raw_log_mechanics_are_refused_before_the_parser_ignores_them(line):
    with pytest.raises(UnsupportedRulesError):
        parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="x", replay_raw_text=line))


def test_replay_metadata_cannot_hide_a_foreign_tier():
    with pytest.raises(UnsupportedRulesError):
        parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="x", replay_json={
            "formatid": "gen9championsvgc2026regmb", "log": "|tier|[Gen 9] VGC 2025 Reg H",
        }))


def test_spectator_chat_is_not_treated_as_a_battle_mechanic():
    parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="x", replay_raw_text=(
        "|player|p1|Alice|\n|poke|p1|Garchomp, L50|\n|c|Spectator|Why no Dynamax?"
    )))


def test_target_nickname_is_not_treated_as_a_battle_mechanic():
    parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="x", replay_raw_text=(
        "|player|p1|Alice|\n|player|p2|Bob|\n"
        "|switch|p1a: Garchomp|Garchomp, L50|100/100\n"
        "|switch|p2a: Dynamax|Incineroar, L50|100/100\n"
        "|move|p1a: Garchomp|Dragon Claw|p2a: Dynamax"
    )))


def test_foreign_strategy_prose_and_fields_never_reach_context():
    cleaned = clean_champions_knowledge({"teraTypes": {"Steel": 1},
        "overview": "Use Tera Blast", "moves": ["Earthquake", "Tera Blast"],
        "sets": [{"item": "Groundium Z", "teratypes": ["Ground"]}],
    })
    assert "teraTypes" not in cleaned
    assert cleaned["overview"] == ""
    assert cleaned["moves"] == ["Earthquake"]
    assert cleaned["sets"] == [{"item": ""}]


def test_generated_mechanics_are_corrected_once_then_blocked():
    calls = []

    def bad(correction):
        calls.append(correction)
        return "Use Dynamax."

    with pytest.raises(UnsupportedRulesError):
        explain_champions_only(bad)
    assert len(calls) == 2
    assert calls[1]
    answers = iter(["Use Tera Steel", "Use Mega Evolution."])
    assert explain_champions_only(lambda _: next(answers)) == "Use Mega Evolution."
