"""Reported three-replay context overflow: bounded, causal model inputs offline."""

import copy
import json

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import InferenceOptions
from src.domain.bo3_inference.narration import coaching_context
from src.domain.exceptions import LLMProviderError
from src.domain.models import SelectionPlan
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_prompt_context import (
    CONTEXT_BYTES,
    MESSAGE_BYTES,
    checked_bo3_message,
    compact_bo3_context,
    encode_context,
)
from src.services.ground_truth import build_explanation_input, build_result
from src.services.series_analysis import Bo3AnalysisService
from tests.conftest import PROMPTS, SAMPLE_DIR, FakeLLM, build_evidence
from tests.test_bo3_context_encoding import expand_evidence
from tests.test_bo3_inference_engine import engine  # noqa: F401
from tests.test_public_teamsheets import sauron_games


def rows(context, key):
    columns = context["context_compaction"].get("turn_check_columns", {}).get(key)
    return [dict(zip(columns, row)) for row in context[key]] if columns else context[key]


@pytest.fixture
def native_forward_calc():
    from pathlib import Path

    from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter

    calc = SmogonCalcAdapter(Path("node_calc/calc_server.js").resolve())
    yield calc
    calc.close()


@pytest.mark.parametrize("native_forward", [False, True])
def test_reported_three_game_series_fits_context_without_losing_order_or_stat_evidence(
    engine, native_forward_calc, native_forward, monkeypatch,  # noqa: F811
):  # noqa: F811
    if native_forward:
        # Exercise lossless fallback with the real damage engine and actual
        # default-budget inference, rather than only metadata and fake rolls.
        monkeypatch.setattr("src.services.bo3_prompt_context.CONTEXT_BYTES", 80_000)
    assembler = build_evidence(SAMPLE_DIR / "gen9championsvgc2026regmb.json",
                              calc_engine=native_forward_calc if native_forward else None)
    per_game_messages = []

    class Pipeline:
        def analyze(self, request):
            evidence = assembler.assemble(request=request, game_state=ShowdownReplayParser().parse(request.replay_json),
                selection=SelectionPlan(), history=[])
            per_game_messages.append(build_explanation_input(request.question, evidence))
            return build_result(request=request, evidence=evidence, answer="Game review.", provider="fake")

    llm = FakeLLM("{}", "Series review.")
    req = Bo3AnalysisRequest(session_id="reported-context-overflow", games=sauron_games(),
        inference_options=InferenceOptions() if native_forward else InferenceOptions(max_assignments=1, max_projections=1))
    result = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline, llm=llm, prompts=PROMPTS,
        inference=Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), public_sheets=ShowdownTeamSheetParser(),
            projection=engine, attributes=engine)).analyze(req)
    text = llm.calls[0]["messages"][-1].content
    encoded_payload = json.loads(text)
    payload = expand_evidence(encoded_payload)
    if native_forward:
        assert "bo3_evidence_encoding" in encoded_payload
        assert payload["trusted_stat_spread_findings"][-1]["eligible_damage_observations"] == 38
    legacy = coaching_context({"question": req.question, "series": result.evidence.model_dump(mode="json"),
        "set_inference": result.inference.model_dump(mode="json"),
        "trusted_stat_spread_findings": [interpret_checkpoint(checkpoint).model_dump(mode="json")
            for checkpoint in result.inference.checkpoints],
        "game_evidence": [game.model_dump(mode="json", exclude={"answer", "question", "session_id", "agent_tool_calls"})
            for game in result.games]})
    assert len(json.dumps(legacy, ensure_ascii=False).encode("utf-8")) > 300_000
    assert len(text.encode("utf-8")) < MESSAGE_BYTES
    assert len(text.encode("utf-8")) < 85_000  # regression ceiling, independent of the safety guard
    assert all(len(message.encode("utf-8")) < MESSAGE_BYTES for message in per_game_messages)
    assert all(len(message.encode("utf-8")) < 75_000 for message in per_game_messages)
    assert len(per_game_messages) == 3 and result.evidence.score == {"sauronMobile": 1, "miro kart ds": 2}
    assert payload["series"] == result.evidence.model_dump(mode="json")
    for original, packed in zip(result.games, payload["game_evidence"]):
        assert packed["battle_result"] == original.battle_result
        assert [(entry["turn"], entry["actor"], entry["move"]) for entry in rows(packed, "turn_checks")] == [
            (entry.turn, entry.actor, entry.move) for entry in original.turn_checks]
        assert "meta_context" not in packed and "strategies" not in packed
        assert any(check.damage_checks for check in original.turn_checks)  # UI DTO retains its full checks
    seen = {}
    for checkpoint in payload["trusted_stat_spread_findings"]:
        for pokemon in checkpoint["pokemon"]:
            if "unchanged_from_checkpoint" in pokemon:
                assert pokemon["pokemon_id"] in seen[pokemon["unchanged_from_checkpoint"]]
        seen[checkpoint["checkpoint_id"]] = {entry["pokemon_id"] for entry in checkpoint["pokemon"]}
    assert result.inference.checkpoints[-1].summaries[0].attributes
    assert payload["context_compaction"]["full_calculated_evidence_retained_in_results"]


def test_compaction_keeps_facts_and_question_immutable_and_is_idempotent():
    source = {"question": "Explain the losing player's decisions", "turn_checks": [{"turn": 1,
        "actor_player": "p2", "move": "Protect", "conditions": ["Trick Room"],
        "damage_checks": [{"actual_hp_remaining_percent": 0, "projected_max_percent": 99,
                           "description": "repeated calculator prose"}]}]}
    original = copy.deepcopy(source)
    packed = compact_bo3_context(source)
    assert source == original and compact_bo3_context(packed) == packed
    assert packed["question"] == source["question"]
    assert rows(packed, "turn_checks")[0]["damage_checks"][0] == {"actual_hp_remaining_percent": 0, "projected_max_percent": 99}
    assert rows(packed, "turn_checks")[0]["conditions"] == ["Trick Room"]


def test_known_empty_conditions_and_effects_do_not_become_unknown_nulls():
    packed = compact_bo3_context({"turn_checks": [{"turn": 1, "conditions": [], "effects": [],
        "actor_hp_percent": 0, "note": ""}]})
    check = rows(packed, "turn_checks")[0]
    assert check["conditions"] == [] and check["effects"] == []
    assert check["actor_hp_percent"] == 0 and check["note"] == ""


def test_budget_reduces_only_optional_alternatives_and_marks_what_was_omitted():
    alternatives = [{"move": "Protect", "summary": f"Option {index}: " + "optional advice " * 300} for index in range(30)]
    packed = compact_bo3_context({"turn_by_turn_checks": [{"turn": 1, "move": "Dragon Claw",
        "damage_checks": [{"actual_result": "fainted"}], "best_alternatives": alternatives}]})
    check = rows(packed, "turn_by_turn_checks")[0]
    assert len(check["best_alternatives"]) == 1 and check["best_alternatives_omitted_count"] == 29
    assert check["damage_checks"] == [{"actual_result": "fainted"}]
    assert len(encode_context(packed).encode("utf-8")) < CONTEXT_BYTES


def test_excessive_required_evidence_or_question_fails_before_provider_without_truncation():
    source = {"battle_result": "ordered battle evidence " * 5000}
    original = copy.deepcopy(source)
    with pytest.raises(LLMProviderError, match="chronological evidence was not truncated"):
        compact_bo3_context(source)
    assert source == original
    with pytest.raises(LLMProviderError, match="shorter question"):
        checked_bo3_message("あ" * 40_000)  # byte budget, not a guessed characters/token ratio


def test_repeated_caveats_are_losslessly_referenced_in_the_same_game_scope():
    caveat = "Projected values use assumed sets and are not observed hidden stats. " * 4
    source = {"turn_checks": [{"turn": 1, "stat_caveat": caveat}, {"turn": 2, "stat_caveat": caveat}]}
    packed = compact_bo3_context(source)
    for row in rows(packed, "turn_checks"):
        reference = row["stat_caveat"]["shared_check_text_ref"]
        assert packed["shared_check_text"][reference] == caveat
    assert source["turn_checks"][0]["stat_caveat"] == caveat


def test_oversized_essential_series_evidence_never_reaches_explanation_provider():
    from src.domain.models import AnalysisResult, MetaContext
    from tests.test_bo3_analysis import game

    class Pipeline:
        def analyze(self, request):
            return AnalysisResult(session_id=request.session_id, question=request.question, answer="Review",
                selection=SelectionPlan(), meta_context=MetaContext(), battle_result="ordered evidence " * 10_000)

    llm = FakeLLM("{}", "Must not be called")
    service = Bo3AnalysisService(parser=ShowdownReplayParser(), pipeline_factory=Pipeline, llm=llm, prompts=PROMPTS)
    with pytest.raises(LLMProviderError, match="chronological evidence was not truncated"):
        service.analyze(Bo3AnalysisRequest(session_id="oversized", games=[game()]))
    assert not llm.calls
