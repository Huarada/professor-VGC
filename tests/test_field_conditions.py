"""Tests for field/status extraction and field-aware speed context."""

from __future__ import annotations

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import (
    CalcField,
    FieldWindow,
    GameState,
    MetaContext,
    PokemonSet,
    SelectionPlan,
)
from src.services.battle_context import outcome_summary
from src.services.matchup_evaluator import MatchupEvaluator
from tests.test_battle_state_per_move import _RecordingCalc

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
    assert field.tailwind == {"p1": [[1, 2]]}
    assert field.tailwind_active("p1", 1)
    assert not field.tailwind_active("p2", 1)


def test_weather_is_a_translated_turn_window():
    field = _state().field
    # Still up when the log ended: turn 2 is where the log stopped, not an expiry.
    assert field.weather == [FieldWindow(name="Sun", start_turn=1, end_turn=2, open_at_end=True)]
    assert field.weather_on(1) == ["Sun"]
    assert field.weather_on(0) == []


def test_timeline_annotates_conditions():
    summary = outcome_summary(_state())
    assert "Tailwind p1" in summary
    assert "weather Sun" in summary
    assert "Weather became Sun." in summary
    assert "p2 Garchomp was afflicted with paralysis." in summary


def test_whole_game_verdict_uses_a_neutral_baseline():
    """Reported: a whole-game verdict was computed under the weather/terrain that
    lasted longest, any Tailwind/Trick Room ever set and the status at the END
    of the game — conditions the two Pokemon may never have faced together.
    This game had p1 Tailwind, sun and a late paralysis on Garchomp."""
    calc = _RecordingCalc()
    state = _state()
    MatchupEvaluator(calc).evaluate(
        state,
        SelectionPlan(matchups=[("Garchomp", "Talonflame"), ("Talonflame", "Garchomp")]),
        MetaContext(),
    )
    assert calc.requests  # both matchups were calculated
    for request in calc.requests:
        assert request.field == CalcField()
        assert request.attacker.status is None
        assert request.defender.status is None


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


def _turn_header(state: GameState, turn: int) -> str:
    prefix = "Leads" if turn == 0 else f"Turn {turn}"
    return next(line for line in outcome_summary(state).splitlines() if line.startswith(prefix))


def test_timeline_header_shows_a_replacement_never_two_at_once():
    """Reported: a turn where rain replaced sun (or Grassy replaced Electric
    Terrain) read 'weather Sun; weather Rain' — as if both were up together."""
    state = ShowdownReplayParser().parse("\n".join([
        "|player|p1|Ash|", "|player|p2|Gary|", "|start",
        "|switch|p1a: Torkoal|Torkoal, L50|100/100",
        "|switch|p2a: Pincurchin|Pincurchin, L50|100/100",
        "|-weather|SunnyDay|[from] ability: Drought|[of] p1a: Torkoal",
        "|-fieldstart|move: Electric Terrain|[from] ability: Electric Surge|[of] p2a: Pincurchin",
        "|turn|1",
        "|switch|p2a: Pelipper|Pelipper, L50|100/100",
        "|-weather|RainDance|[from] ability: Drizzle|[of] p2a: Pelipper",
        "|switch|p1a: Rillaboom|Rillaboom, L50|100/100",
        "|-fieldstart|move: Grassy Terrain|[from] ability: Grassy Surge|[of] p1a: Rillaboom",
        "|move|p1a: Rillaboom|Grassy Glide|p2a: Pelipper",
        "|-damage|p2a: Pelipper|60/100",
        "|turn|2",
        "|move|p1a: Rillaboom|Grassy Glide|p2a: Pelipper",
        "|-damage|p2a: Pelipper|20/100",
        "|-weather|none",
        "|-fieldend|move: Grassy Terrain",
        "|turn|3",
        "|move|p1a: Rillaboom|Wood Hammer|p2a: Pelipper",
        "|-damage|p2a: Pelipper|0 fnt",
    ]))
    assert _turn_header(state, 1) == "Turn 1 [weather Sun → Rain; terrain Electric → Grassy]:"
    assert _turn_header(state, 2) == (
        "Turn 2 [weather Rain (ended this turn); terrain Grassy (ended this turn)]:"
    )
    assert _turn_header(state, 3) == "Turn 3:"


def test_timeline_header_does_not_call_the_log_end_an_expiry():
    # The fixture's sun is still up when the log stops on turn 2.
    assert "ended this turn" not in _turn_header(_state(), 2)
