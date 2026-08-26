"""Tests for the 1st-AI selection service."""

from __future__ import annotations

import json

from src.domain.models import AnalysisRequest, GameState, PokemonSet, SideState
from src.services.selection_service import LLMSelectionService
from tests.conftest import FakeLLM


def _state() -> GameState:
    return GameState(
        sides=[
            SideState(player="p1", team=[PokemonSet(species="Garchomp")]),
            SideState(player="p2", team=[PokemonSet(species="Sinistcha")]),
        ]
    )


def test_parses_valid_selection_json():
    llm = FakeLLM(
        selection_json=json.dumps(
            {"focus_species": ["Garchomp", "Sinistcha"],
             "matchups": [["Garchomp", "Sinistcha"]], "rationale": "offensive check"}
        ),
        explanation="",
    )
    plan = LLMSelectionService(llm).select(
        request=AnalysisRequest(session_id="s", question="who wins"),
        game_state=_state(), history=[],
    )
    assert plan.matchups == [("Garchomp", "Sinistcha")]
    assert plan.rationale == "offensive check"


def test_filters_hallucinated_species():
    llm = FakeLLM(
        selection_json=json.dumps(
            {"focus_species": ["Pikachu"], "matchups": [["Pikachu", "Garchomp"]]}
        ),
        explanation="",
    )
    plan = LLMSelectionService(llm).select(
        request=AnalysisRequest(session_id="s"), game_state=_state(), history=[]
    )
    assert all(a != "Pikachu" and b != "Pikachu" for a, b in plan.matchups)


def test_unparseable_output_falls_back():
    plan = LLMSelectionService(FakeLLM("not json at all", "")).select(
        request=AnalysisRequest(session_id="s"), game_state=_state(), history=[]
    )
    assert plan.matchups
