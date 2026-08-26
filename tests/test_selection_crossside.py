"""Tests for the cross-side matchup guardrail in selection logic."""

from __future__ import annotations

from src.domain.models import SelectionPlan
from src.services.selection_logic import cross_side_matchups, sanitize_plan

_SIDE_OF = {"Torkoal": "p1", "Venusaur": "p1", "Aerodactyl": "p2", "Metagross": "p2"}


def test_drops_same_side_matchups():
    plan = SelectionPlan(
        focus_species=["Torkoal", "Venusaur"],
        matchups=[("Torkoal", "Venusaur"), ("Torkoal", "Aerodactyl")],
        rationale="x",
    )
    out = sanitize_plan(plan, list(_SIDE_OF), 6, _SIDE_OF)
    assert ("Torkoal", "Venusaur") not in out.matchups  # same side dropped
    assert ("Torkoal", "Aerodactyl") in out.matchups  # cross side kept


def test_fills_cross_side_when_empty():
    plan = SelectionPlan(focus_species=[], matchups=[("Torkoal", "Venusaur")], rationale="")
    out = sanitize_plan(plan, list(_SIDE_OF), 6, _SIDE_OF)
    assert out.matchups
    for a, b in out.matchups:
        assert _SIDE_OF[a] != _SIDE_OF[b]


def test_cross_side_matchups_helper():
    pairs = cross_side_matchups(_SIDE_OF, limit=10)
    assert all(_SIDE_OF[a] != _SIDE_OF[b] for a, b in pairs)
