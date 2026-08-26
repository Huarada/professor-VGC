"""Tests for the official @pkmn/smogon integration (no network; fake IPC)."""

from __future__ import annotations

import pytest

from src.adapters.smogon.composite_strategy import CompositeStrategyProvider
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter
from src.domain.exceptions import StrategyKnowledgeError
from src.domain.models import SmogonStrategy
from src.services.suggestion_service import build_improvement_context, wants_suggestions


class FakeIpc:
    """Returns canned Node responses keyed by command."""

    def __init__(self, responses: dict):
        self._responses = responses

    def request(self, payload):
        return self._responses.get(payload["cmd"], {"ok": False, "error": "no data"})

    def close(self):  # pragma: no cover
        pass


_ANALYSES = {
    "ok": True,
    "result": [
        {
            "format": "gen9vgc2024",
            "overview": "Garchomp is a fast physical attacker.",
            "comments": "Great with Tailwind support.",
            "sets": [
                {
                    "name": "Scarf",
                    "ability": "Rough Skin",
                    "item": "Choice Scarf",
                    "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"],
                }
            ],
        }
    ],
}
_STATS = {
    "ok": True,
    "result": {
        "count": 1000,
        "abilities": {"Rough Skin": 900, "Sand Veil": 100},
        "items": {"Choice Scarf": 400, "Life Orb": 300},
        "moves": {"Earthquake": 800, "Dragon Claw": 700},
        "spreads": {"Jolly:0/252/0/0/4/252": 500},
        "teammates": {"Incineroar": 300, "Rillaboom": 200},
        "counters": {"Aurorus": [50, 0.51, 0.2]},
        "teraTypes": {"Steel": 400},
    },
}
_SETS = {"ok": True, "result": [{"species": "Garchomp", "item": "Choice Scarf",
         "ability": "Rough Skin", "moves": ["Earthquake", "Dragon Claw"]}]}


def _adapter(**responses):
    return SmogonDexAdapter(ipc=FakeIpc(responses))


def test_get_strategy_from_official_analyses():
    strat = _adapter(analyses=_ANALYSES).get_strategy("Garchomp")
    assert "[Smogon official" in strat.overview
    assert "fast physical attacker" in strat.overview
    assert strat.common_sets


def test_get_stats_maps_usage():
    summary = _adapter(stats=_STATS).get_stats("Garchomp")
    assert "Rough Skin" in summary.top_abilities
    assert "Choice Scarf" in summary.top_items
    assert summary.top_spreads and "252 Atk" in summary.top_spreads[0]
    assert "official" in summary.source


def test_missing_analyses_raises():
    with pytest.raises(StrategyKnowledgeError):
        _adapter().get_strategy("Garchomp")


def test_composite_falls_back_to_chaos(sample_chaos_path):
    from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter

    dex = _adapter()  # no responses -> primary always fails
    composite = CompositeStrategyProvider(primary=dex, fallback=ChaosStrategyAdapter(sample_chaos_path))
    strat = composite.get_strategy("Garchomp")
    assert isinstance(strat, SmogonStrategy)
    assert "Garchomp" == strat.species and strat.overview  # from chaos fallback


def test_composite_prefers_official(sample_chaos_path):
    from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter

    dex = _adapter(analyses=_ANALYSES)
    composite = CompositeStrategyProvider(primary=dex, fallback=ChaosStrategyAdapter(sample_chaos_path))
    assert "Smogon official" in composite.get_strategy("Garchomp").overview


def test_suggestion_intent_detection():
    assert wants_suggestions("How can I improve my team's synergy?")
    assert wants_suggestions("sugere um ajuste no moveset do Garchomp?")
    assert not wants_suggestions("Who won the game?")


def test_build_improvement_context_uses_sets_and_stats():
    source = _adapter(sets=_SETS, stats=_STATS)
    ctx = build_improvement_context(source, ["Garchomp"], "gen9vgc2024")
    assert "Garchomp" in ctx
    assert ctx["Garchomp"]["official_sets"]
    assert ctx["Garchomp"]["teammates_usage"]
    assert ctx["Garchomp"]["usage_stats"]["top_items"]
