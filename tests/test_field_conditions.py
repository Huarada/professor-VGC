"""Tests for field/status extraction and field-aware speed context."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import GameState, SelectionPlan
from src.services.battle_context import outcome_summary
from src.services.matchup_evaluator import MatchupEvaluator

_LOG = (
    '{"formatid":"gen9vgc2026","log":"'
    "|player|p1|Ash|1|1|\\n|player|p2|Gary|2|1|\\n"
    "|poke|p1|Talonflame, L50|\\n|poke|p2|Garchomp, L50|\\n|start\\n"
    "|switch|p1a: Talonflame|Talonflame, L50|100/100\\n"
    "|switch|p2a: Garchomp|Garchomp, L50|100/100\\n|turn|1\\n"
    "|move|p1a: Talonflame|Tailwind|p1a: Talonflame\\n"
    "|-sidestart|p1: Ash|move: Tailwind\\n"
    "|move|p2a: Garchomp|Stomping Tantrum|p1a: Talonflame\\n"
    "|-damage|p1a: Talonflame|40/100\\n|-weather|SunnyDay\\n|turn|2\\n"
    "|-status|p2a: Garchomp|par\\n|win|Ash\\n"
    '"}'
)


def _state() -> GameState:
    return ShowdownReplayParser().parse(_LOG)


def test_tailwind_window_extracted():
    field = _state().field
    assert field is not None
    assert field.had_tailwind("p1")
    assert field.tailwind_active("p1", 1)
    assert not field.had_tailwind("p2")


def test_weather_and_status_extracted():
    field = _state().field
    assert field.weather == "SunnyDay"
    assert field.statuses.get("Garchomp") == "par"


def test_timeline_annotates_conditions():
    summary = outcome_summary(_state())
    assert "Tailwind p1" in summary


def test_field_for_builds_spec():
    st = _state()
    spec = MatchupEvaluator._field_for(st, "p1", "p2")
    assert spec.get("attackerTailwind") is True
    assert "defenderTailwind" not in spec
    assert spec.get("weather") == "SunnyDay"


def test_status_applied_to_enriched_set():
    from src.domain.models import MetaContext, PokemonSet
    from src.services.matchup_evaluator import MatchupEvaluator as ME

    ev = ME(calc_engine=None)  # enrich_set does not touch the engine
    mon = PokemonSet(species="Garchomp")
    enriched = ev.enrich_set(mon, MetaContext(), {"Garchomp": "par"})
    assert enriched.status == "par"
