"""Tests for the shared archetype-inference signal table.

Reported: a Perish-Trap-style core built around a Pokemon whose trapping
comes from its ABILITY (Shadow Tag, Arena Trap, Magnet Pull — e.g. Mega
Gengar, which gains Shadow Tag only upon Mega Evolving) was never
detected, because the archetype inference only ever scanned MOVE names —
including a "shadowtag" entry that could never match anything, since
Shadow Tag is never a move. This is the fixed, single shared
implementation both strategy providers (Chaos-derived and official
Smogon) now call.
"""

from __future__ import annotations

import json

from src.adapters.smogon.archetype_signals import infer_archetypes
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.models import Archetype


# -- infer_archetypes: pure ------------------------------------------------ #

def test_shadow_tag_ability_signals_perish_trap():
    # The exact bug: "Shadow Tag" was in the old signal table as if it were
    # a move, so it could never match. Confirming it works via the ABILITY
    # argument now.
    assert Archetype.PERISH_TRAP in infer_archetypes([], ["Shadow Tag"])


def test_arena_trap_and_magnet_pull_also_signal_perish_trap():
    assert Archetype.PERISH_TRAP in infer_archetypes([], ["Arena Trap"])
    assert Archetype.PERISH_TRAP in infer_archetypes([], ["Magnet Pull"])


def test_perish_song_move_still_signals_perish_trap_too():
    # The one entry that WAS already correct (Perish Song genuinely is a
    # move) must keep working after the refactor.
    assert Archetype.PERISH_TRAP in infer_archetypes(["Perish Song"], [])


def test_move_and_ability_signals_combine_without_duplicates():
    tags = infer_archetypes(["Perish Song", "Protect"], ["Shadow Tag"])
    assert tags.count(Archetype.PERISH_TRAP) == 1
    assert Archetype.SAFE_SWAPPER in tags


def test_no_signal_falls_back_to_balance():
    assert infer_archetypes(["Tackle"], ["Levitate"]) == [Archetype.BALANCE]


def test_irrelevant_ability_does_not_false_positive():
    assert infer_archetypes([], ["Intimidate"]) == [Archetype.BALANCE]


def test_case_and_formatting_insensitive():
    # "shadow tag", "Shadow-Tag", "SHADOWTAG" must all normalize the same way.
    assert Archetype.PERISH_TRAP in infer_archetypes([], ["shadow tag"])
    assert Archetype.PERISH_TRAP in infer_archetypes([], ["SHADOW-TAG"])


# -- ChaosStrategyAdapter: abilities actually reach infer_archetypes ------ #

def _write_chaos(dirpath, metagame, cutoff, data):
    payload = {"info": {"metagame": metagame}, "data": data}
    (dirpath / f"{metagame}-{cutoff}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_chaos_adapter_surfaces_perish_trap_for_a_shadow_tag_trapper(tmp_path):
    mon = {
        "Raw count": 100,
        "Abilities": {"Shadow Tag": 80, "Cursed Body": 20},
        "Items": {"Gengarite": 100},
        "Moves": {"Protect": 60, "Shadow Ball": 50},
        "Spreads": {},
        "Teammates": {"Archaludon": 40},
        "Checks and Counters": {},
    }
    d = tmp_path / "chaos"
    d.mkdir()
    _write_chaos(d, "gen9championsvgc2026regmb", 1760, {"Gengar": mon})

    strat = ChaosStrategyAdapter(d).get_strategy("Gengar", metagame="gen9championsvgc2026regmb")
    assert Archetype.PERISH_TRAP in strat.archetypes
