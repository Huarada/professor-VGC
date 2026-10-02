"""Regulation controller: data and Pokemon from one regulation never leak into another.

Requirement: pin analyses to Reg M-B or Reg M-C, and make sure nothing from
Reg M-C — usage data, teammates, counters, tool lookups, or a Pokemon the
LLM brings in from memory — ever shows up as part of a Reg M-B analysis.

The fixtures below build two tiny Chaos regulations: Reg M-B and a newer
Reg M-C where "Meganium" exists only in M-C. Every test checks one leak path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.chaos_repository import ChaosRepository, StrictRegulationRepository
from src.adapters.llm.evidence_tools import EvidenceTools
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.config import Settings
from src.domain.exceptions import ChaosDataError, ConfigurationError, RegulationMismatchError
from src.domain.models import AnalysisRequest
from src.domain.regulation import Regulation, RegulationScope, parse_regulation_setting
from src.services.analysis_service import AnalysisService
from src.services.ground_truth import GroundTruthAssembler
from src.services.regulation_guard import RegulationGuard
from src.services.selection_service import LLMSelectionService
from tests.conftest import PROMPTS, FakeCalcEngine, FakeLLM

MB = "gen9championsvgc2026regmb"
MC = "gen9championsvgc2026regmc"


def _mon(teammates: dict[str, float], counters: dict[str, float]) -> dict[str, object]:
    return {
        "Raw count": 100, "Abilities": {"Rough Skin": 100}, "Items": {"Choice Scarf": 100},
        "Moves": {"Earthquake": 100}, "Spreads": {"Jolly:0/32/0/0/0/32": 100},
        "Teammates": teammates,
        "Checks and Counters": {k: {"p": v} for k, v in counters.items()},
    }


def _write(directory: Path, metagame: str, data: dict[str, object], declared: str | None = None) -> None:
    payload = {"info": {"metagame": declared or metagame, "cutoff": 1760}, "data": data}
    (directory / f"{metagame}-1760.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def chaos_dir(tmp_path: Path) -> Path:
    _write(tmp_path, MB, {
        "Garchomp": _mon({"Incineroar": 50}, {"Incineroar": 0.6}),
        "Incineroar": _mon({"Garchomp": 50}, {"Garchomp": 0.5}),
    })
    _write(tmp_path, MC, {
        "Garchomp": _mon({"Meganium": 80, "Incineroar": 20}, {"Meganium": 0.7}),
        "Incineroar": _mon({"Meganium": 60}, {"Meganium": 0.6}),
        "Meganium": _mon({"Garchomp": 70}, {"Garchomp": 0.5}),
    })
    return tmp_path


class _NamesCalc(FakeCalcEngine):
    def species_names(self, gen: int) -> list[str]:
        return ["Garchomp", "Incineroar", "Meganium", "Charizard", "Charizard-Mega-Y", "Ditto"]


_TIER_NAMES = {MB: "[Gen 9 Champions] VGC 2026 Reg M-B", MC: "[Gen 9 Champions] VGC 2026 Reg M-C"}


def _log(format_id: str) -> str:
    # Raw logs carry the display name; the parser turns it into the format id.
    return "\n".join([
        f"|tier|{_TIER_NAMES[format_id]}", "|player|p1|A|", "|player|p2|B|", "|start",
        "|switch|p1a: Garchomp|Garchomp, L50|100/100",
        "|switch|p2a: Incineroar|Incineroar, L50|100/100",
        "|turn|1",
        "|move|p1a: Garchomp|Earthquake|p2a: Incineroar",
        "|-damage|p2a: Incineroar|40/100",
    ])


# -- regulation identity ------------------------------------------------------------


def test_bo3_belongs_to_its_bo1_regulation_and_labels_are_readable():
    mb = Regulation.from_format(MB)
    assert mb is not None and mb.label == "Reg M-B" and mb.code == "mb"
    assert Regulation.from_format(f"{MB}bo3") == mb
    assert mb.includes(f"{MB}bo3") and not mb.includes(MC)
    assert Regulation.from_format("gen9vgc2025regh").label == "Reg H"
    assert Regulation.from_format("gen9ou") is None


def test_controller_setting_accepts_auto_codes_and_full_ids_only():
    prefix = "gen9championsvgc2026reg"
    assert parse_regulation_setting("auto", prefix) is None
    assert parse_regulation_setting("MC", prefix).format_id == MC
    assert parse_regulation_setting(f"{MB}bo3", prefix).format_id == MB
    with pytest.raises(ConfigurationError):
        parse_regulation_setting("not a regulation 1", prefix)
    assert Settings(_env_file=None, regulation="mb").regulation == "mb"
    with pytest.raises(ValueError):
        Settings(_env_file=None, regulation="xyz123")


def test_pinned_scope_refuses_a_replay_from_another_regulation():
    scope = RegulationScope(Regulation.from_format(MB))
    assert scope.bind(f"{MB}bo3").format_id == MB  # Bo3 of the same regulation is fine
    with pytest.raises(RegulationMismatchError, match="Reg M-C"):
        scope.bind(MC)
    auto = RegulationScope()
    assert auto.bind(MC).format_id == MC and not auto.strict


# -- data sources ---------------------------------------------------------------------


def test_a_requested_regulation_is_never_swapped_for_the_newest(tmp_path: Path):
    _write(tmp_path, MC, {"Garchomp": _mon({"Meganium": 80}, {"Meganium": 0.7})})
    context = ChaosAdapter(tmp_path).build_match_context(["Garchomp"], metagame=MB)
    assert context.pokemon_stats["Garchomp"].is_empty()  # MB not loaded: nothing, not MC


def test_tiers_whose_data_names_another_format_are_rejected(chaos_dir: Path):
    _write(chaos_dir, "gen9championsvgc2026regma", {"Garchomp": _mon({}, {})},
           declared="gen9championsbssregma")
    repo = ChaosRepository(chaos_dir)
    assert "gen9championsvgc2026regma-1760.json" in repo.rejected_tiers
    assert "gen9championsvgc2026regma" not in repo.metagames()


def test_only_rejected_tiers_is_an_error(tmp_path: Path):
    _write(tmp_path, MB, {"Garchomp": _mon({}, {})}, declared="gen9championsbssregmb")
    with pytest.raises(ChaosDataError, match="gen9championsbssregmb"):
        ChaosRepository(tmp_path)


def test_strict_view_never_falls_back_to_another_regulation(chaos_dir: Path):
    # A species present in Reg M-B but absent from Reg M-C shows the fallback:
    _write(chaos_dir, MB, {"Garchomp": _mon({}, {}), "Charizard": _mon({}, {})})
    repo = ChaosRepository(chaos_dir)
    assert repo.resolve_mon(MC, "Charizard") is not None  # auto: older-regulation fallback
    assert StrictRegulationRepository(repo).resolve_mon(MC, "Charizard") is None


def test_legal_species_come_from_the_regulation_alone(chaos_dir: Path):
    adapter = ChaosAdapter(chaos_dir)
    assert "meganium" not in adapter.legal_species(MB)
    assert "meganium" in adapter.legal_species(MC)
    mb = adapter.roster(MB)
    assert not mb.allows("Meganium") and mb.allows("Garchomp")
    assert adapter.roster(MC).allows("Meganium")


def test_a_forme_listed_by_another_regulation_is_not_waved_through_as_its_base():
    """Real case (Sept 2026 data): Garchomp-Mega-Z exists only in Reg M-C while
    Garchomp is legal in Reg M-B — the Mega must not pass as plain Garchomp."""
    from src.domain.regulation import RegulationRoster

    mb = RegulationRoster(
        legal=frozenset({"garchomp", "palafin"}),
        known=frozenset({"garchomp", "palafin", "garchompmegaz"}),
    )
    assert not mb.allows("Garchomp-Mega-Z")
    assert mb.allows("Palafin-Hero")  # battle-only forme the data never lists alone
    assert not mb.allows("Mewtwo")  # unknown to every regulation: not legal


# -- evidence, tools and the answer ----------------------------------------------------


def _assembler(chaos_dir: Path, pinned: str | None) -> GroundTruthAssembler:
    scope = RegulationScope(Regulation.from_format(pinned) if pinned else None)
    repo = ChaosRepository(chaos_dir)
    chaos = ChaosAdapter(repository=StrictRegulationRepository(repo) if scope.strict else repo)
    calc = _NamesCalc()
    return GroundTruthAssembler(
        meta_provider=chaos, calc_engine=calc,
        strategy_provider=ChaosStrategyAdapter(repository=repo),
        scope=scope, regulation_catalog=chaos, guard=RegulationGuard(calc),
    )


def test_evidence_is_scoped_and_filtered_to_the_regulation(chaos_dir: Path):
    assembler = _assembler(chaos_dir, MB)
    request = AnalysisRequest(session_id="s", replay_raw_text=_log(MB), question="q")
    state = assembler.prepare(ShowdownReplayParser(), request)
    from src.domain.models import SelectionPlan

    evidence = assembler.assemble(request=request, game_state=state,
                                  selection=SelectionPlan(), history=[])
    assert evidence.regulation is not None and evidence.regulation.label == "Reg M-B"
    assert evidence.regulation.strict and "Meganium" not in evidence.regulation.legal_species
    dumped = json.dumps(evidence.model_dump(mode="json"))
    assert "Meganium" not in dumped  # no M-C teammate, counter or species anywhere


def test_auto_mode_follows_the_replay_regulation(chaos_dir: Path):
    assembler = _assembler(chaos_dir, None)
    request = AnalysisRequest(session_id="s", replay_raw_text=_log(MC), question="q")
    assembler.prepare(ShowdownReplayParser(), request)
    assert assembler.scope.format_id == MC and not assembler.scope.strict


def test_answers_naming_pokemon_from_another_regulation_are_corrected(chaos_dir: Path):
    assembler = _assembler(chaos_dir, MB)
    request = AnalysisRequest(session_id="s", replay_raw_text=_log(MB), question="q")
    state = assembler.prepare(ShowdownReplayParser(), request)
    from src.domain.models import SelectionPlan

    evidence = assembler.assemble(request=request, game_state=state,
                                  selection=SelectionPlan(), history=[])
    notes: list[str] = []

    def explain(correction: str) -> str:
        notes.append(correction)
        return "Pair Garchomp with Meganium." if not correction else "Pair Garchomp with Incineroar."

    answer, warnings = assembler.explain_within_regulation(explain, evidence, state)
    assert answer == "Pair Garchomp with Incineroar." and warnings == []
    assert notes[0] == "" and "Meganium" in notes[1] and "Reg M-B" in notes[1]

    stubborn, warnings = assembler.explain_within_regulation(
        lambda correction: "Meganium is great here.", evidence, state
    )
    assert warnings and "Meganium is not legal in Reg M-B" in warnings[0]


def test_guard_trims_formes_and_ignores_lowercase_words():
    from src.domain.regulation import RegulationRoster

    guard = RegulationGuard(_NamesCalc())
    roster = RegulationRoster(
        legal=frozenset({"garchomp", "charizard", "charizardmegay"}),
        known=frozenset({"garchomp", "charizard", "charizardmegay", "meganium"}),
    )
    assert guard.violations("Charizard-Mega-Y and Garchomp, ditto", roster) == []
    assert guard.violations("Use Meganium.", roster) == ["Meganium"]
    assert guard.violations("Use Meganium.", roster, also_allowed=["Meganium"]) == []


def test_agent_tools_stay_inside_the_regulation(chaos_dir: Path):
    repo = ChaosRepository(chaos_dir)
    chaos = ChaosAdapter(repository=StrictRegulationRepository(repo))
    scope = RegulationScope(Regulation.from_format(MB))
    scope.bind(MB)
    tools = EvidenceTools(
        calc_engine=FakeCalcEngine(), meta_provider=chaos,
        strategy_provider=ChaosStrategyAdapter(repository=repo),
        scope=scope, regulation_catalog=chaos,
    )
    refused = tools.chaos_meta_stats(["Meganium"])
    assert refused["ok"] is False and "not legal in Reg M-B" in refused["error"]
    assert tools.damage_calc("Meganium", "Garchomp", "Giga Drain", "", "")["ok"] is False
    stats = tools.chaos_meta_stats(["Garchomp"])
    assert stats["ok"] is True and stats["metagame"] == MB
    assert "Meganium" not in json.dumps(stats)


def test_pinned_pipeline_refuses_another_regulations_replay_before_any_llm_call(chaos_dir: Path):
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": []}), "answer")
    service = AnalysisService(
        parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
        evidence=_assembler(chaos_dir, MB), llm=llm,
        memory=InMemoryConversationMemory(), prompts=PROMPTS,
    )
    with pytest.raises(RegulationMismatchError):
        service.analyze(AnalysisRequest(session_id="s", replay_raw_text=_log(MC), question="q"))
    assert llm.calls == []


def test_raw_logs_carry_their_regulation_including_bo3():
    parser = ShowdownReplayParser()
    assert parser.parse(_log(MB)).format_id == MB
    bo3 = parser.parse("|tier|[Gen 9 Champions] VGC 2026 Reg M-C (Bo3)\n|player|p1|A|\n|poke|p1|Garchomp, L50|")
    assert bo3.format_id == f"{MC}bo3" and Regulation.from_format(bo3.format_id).format_id == MC
