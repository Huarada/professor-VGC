"""Tests for the framework-agnostic agent tool core and its two thin wrappers.

The three on-demand tools used to be implemented twice (once per agent
framework). They now live once in ``EvidenceTools``; ADK passes the bound
methods through and LangChain wraps them as StructuredTools.
"""

from __future__ import annotations

import inspect

import pytest

from src.adapters.llm.adk_tools import build_adk_tools
from src.domain.exceptions import CalcEngineError
from src.domain.models import CalcRequest, DamageResult
from tests.conftest import FakeCalcEngine, build_tools


class _FailingCalc(FakeCalcEngine):
    def calculate(self, request: CalcRequest) -> DamageResult:
        raise CalcEngineError(f"unknown move: {request.move}")


def test_damage_calc_success_and_degrade(sample_chaos_path):
    ok = build_tools(sample_chaos_path).damage_calc("Garchomp", "Sinistcha", "Earthquake", "", "")
    assert ok["ok"] is True and ok["move"] == "Earthquake"
    failed = build_tools(sample_chaos_path, _FailingCalc()).damage_calc(
        "Garchomp", "Sinistcha", "Bogus", "Life Orb", ""
    )
    assert failed == {"ok": False, "error": "unknown move: Bogus"}
    blank = build_tools(sample_chaos_path).damage_calc("Garchomp", "Sinistcha", " ", "", "")
    assert blank["ok"] is False  # invalid input degrades too, never raises into the agent


def test_meta_and_strategy_tools(sample_chaos_path):
    tools = build_tools(sample_chaos_path)
    meta = tools.chaos_meta_stats(["Garchomp"])
    assert meta["ok"] is True and "Garchomp" in meta["pokemon_stats"]
    assert tools.smogon_strategy("Garchomp")["ok"] is True


def test_adk_tools_are_gemini_safe_functions(sample_chaos_path):
    functions = build_adk_tools(build_tools(sample_chaos_path))
    assert [f.__name__ for f in functions] == ["damage_calc", "chaos_meta_stats", "smogon_strategy"]
    for function in functions:
        assert function.__doc__ and "Args:" in function.__doc__
        for param in inspect.signature(function).parameters.values():
            assert param.default is inspect.Parameter.empty  # Gemini rejects defaults


def test_langchain_tools_wrap_the_same_core(sample_chaos_path):
    pytest.importorskip("langchain_core")
    from src.adapters.llm.langchain_tools import build_langchain_tools

    tools = {t.name: t for t in build_langchain_tools(build_tools(sample_chaos_path))}
    assert set(tools) == {"damage_calc", "chaos_meta_stats", "smogon_strategy"}
    result = tools["damage_calc"].invoke(
        {"attacker_species": "Garchomp", "defender_species": "Sinistcha", "move": "Earthquake"}
    )
    assert result["ok"] is True and result["move"] == "Earthquake"
