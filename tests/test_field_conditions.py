"""Tests for field/status extraction and field-aware speed context."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import CalcField, FieldWindow, GameState, MetaContext, PokemonSet
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


def test_weather_is_a_translated_turn_window_and_status_is_end_of_game_only():
    field = _state().field
    assert field.weather == [FieldWindow(name="Sun", start_turn=1, end_turn=2)]
    assert field.weather_on(1) == ["Sun"]
    assert field.weather_on(0) == []
    assert field.final_statuses == {"p2": {"Garchomp": "par"}}


def test_timeline_annotates_conditions():
    summary = outcome_summary(_state())
    assert "Tailwind p1" in summary
    assert "weather Sun" in summary
    assert "Weather became Sun." in summary
    assert "p2 Garchomp was afflicted with paralysis." in summary


def test_field_for_builds_spec():
    spec = MatchupEvaluator._field_for(_state(), "p1", "p2")
    assert isinstance(spec, CalcField)
    assert spec.attacker_side.tailwind is True
    assert spec.defender_side.tailwind is False
    assert spec.weather == "Sun"


def test_status_applied_to_enriched_set():
    ev = MatchupEvaluator(calc_engine=None)  # type: ignore[arg-type] - enrich_set never calls it
    enriched = ev.enrich_set(PokemonSet(species="Garchomp"), MetaContext(), status="par")
    assert enriched.status == "par"


def test_enrich_set_confirmed_no_item_blocks_the_chaos_backfill(sample_chaos_path):
    from src.adapters.chaos.chaos_adapter import ChaosAdapter

    meta = ChaosAdapter(sample_chaos_path).build_match_context(["Garchomp"])
    ev = MatchupEvaluator(calc_engine=None)  # type: ignore[arg-type]
    guessed = ev.enrich_set(PokemonSet(species="Garchomp"), meta)
    consumed = ev.enrich_set(PokemonSet(species="Garchomp"), meta, item="")
    assert guessed.item  # Chaos back-fill when nothing is known
    assert consumed.item is None  # consumed/knocked off: no guess
