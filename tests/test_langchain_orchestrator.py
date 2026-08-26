"""End-to-end test of the LangChain LCEL orchestrator (fake chat model).

LangChain is a normal top-level dependency (pyproject.toml/requirements.txt)
and installed by the documented `pip install -r requirements.txt` setup —
but this whole file's own module-level imports pull in langchain_core
directly, so a minimal/partial install missing it would otherwise fail at
COLLECTION time (an error, not a skip) for every test here. Guarded with
importorskip so that specific, well-known case degrades to a clean skip
instead, matching this project's own test_calc_engine_*.py convention for
Node-unavailable environments.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.exceptions import CalcEngineError, LLMProviderError
from src.domain.interfaces import AnalysisPipeline
from src.domain.models import AnalysisRequest
from src.services.langchain_orchestrator import LangChainAnalysisOrchestrator
from tests.conftest import FakeCalcEngine


class _ToolCapableFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel.bind_tools() raises NotImplementedError — but
    create_agent() (ADR-028) always calls it to attach the explanation
    stage's tool schemas, even on a turn that never actually calls a tool.
    Overridden to a no-op (return self unchanged: this fake ignores the tool
    schemas and just serves its next scripted message regardless) so every
    test below can build a real agent instead of erroring at construction."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 - test double
        return self


def _fake_model(selection_json: str, explanation: str) -> GenericFakeChatModel:
    return _ToolCapableFakeChatModel(
        messages=iter([AIMessage(content=selection_json), AIMessage(content=explanation)])
    )


def _build(chaos_path, model) -> LangChainAnalysisOrchestrator:
    return LangChainAnalysisOrchestrator(
        parser=ShowdownReplayParser(), chat_model=model,
        meta_provider=ChaosAdapter(chaos_path), calc_engine=FakeCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(chaos_path),
        memory=InMemoryConversationMemory(), provider_name="langchain:fake",
    )


def test_orchestrator_satisfies_pipeline_port(sample_chaos_path):
    assert isinstance(_build(sample_chaos_path, _fake_model("{}", "x")), AnalysisPipeline)


def test_full_langchain_pipeline(sample_chaos_path, sample_replay_path):
    model = _fake_model(
        selection_json=json.dumps(
            {"focus_species": ["Garchomp", "Sinistcha"],
             "matchups": [["Garchomp", "Sinistcha"]], "rationale": "lcel"}
        ),
        explanation="Garchomp threatens a 2HKO; Sinistcha is the safe pivot.",
    )
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(
        AnalysisRequest(session_id="lc-1", replay_json=replay,
                        question="Does Garchomp beat Sinistcha?")
    )
    assert result.provider == "langchain:fake"
    assert result.answer.startswith("Garchomp")
    assert result.verdicts
    v = result.verdicts[0]
    assert v.attacker == "Garchomp" and v.defender == "Sinistcha"
    assert "Garchomp" in result.meta_context.pokemon_stats


def test_unparseable_selection_falls_back(sample_chaos_path, sample_replay_path):
    orch = _build(sample_chaos_path, _fake_model("not json", "ok"))
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="lc-2", replay_json=replay, question="q"))
    assert result.verdicts


def _raising_model(*_args, **_kwargs):
    """Stands in for a LangChain chat model whose underlying SDK call fails
    (rate limit, exhausted quota, auth, network, ...) — a plain raising
    callable is coerced into a Runnable by LCEL's `|`, same as any chat model."""
    raise RuntimeError("insufficient_quota: credit_balance_exhausted")


def test_explanation_provider_failure_is_wrapped_not_left_raw(
    sample_chaos_path, sample_replay_path
):
    """Reported: an OpenAI 429/insufficient_quota error reached the Streamlit
    UI as a raw, unhandled SDK traceback instead of the ProfessorVGCError the
    presentation layer already knows how to render — because the LCEL
    explanation chain talks to the chat model directly, bypassing
    OpenAIProvider/GeminiProvider's own error wrapping entirely."""
    orch = _build(sample_chaos_path, _raising_model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    with pytest.raises(LLMProviderError, match="explanation model call failed"):
        orch.analyze(AnalysisRequest(session_id="lc-3", replay_json=replay, question="q"))


# --- ADR-028: agentic follow-up (explanation stage as a bounded tool-calling
# agent) -----------------------------------------------------------------


def _fake_model_with_tool_call(
    selection_json: str, tool_name: str, tool_args: dict, final_answer: str
) -> _ToolCapableFakeChatModel:
    return _ToolCapableFakeChatModel(
        messages=iter(
            [
                AIMessage(content=selection_json),
                AIMessage(content="", tool_calls=[{"name": tool_name, "args": tool_args, "id": "call_1"}]),
                AIMessage(content=final_answer),
            ]
        )
    )


class _SelectivelyFailingCalcEngine(FakeCalcEngine):
    """FakeCalcEngine that raises CalcEngineError for one sentinel move name,
    so a scripted tool call can exercise the {"ok": False, "error": ...}
    degrade path (langchain_tools.py) without disturbing the deterministic
    verdicts/turn-checks the same orchestrator run also computes from the
    sample replay's own real moves."""

    def calculate(self, request):
        if request.move == "Nonexistentmove":
            raise CalcEngineError("unknown move: Nonexistentmove")
        return super().calculate(request)


def test_explanation_agent_calls_a_tool_and_the_result_is_flagged(
    sample_chaos_path, sample_replay_path
):
    """The explanation agent may reach back into damage_calc mid-answer for a
    hypothetical the precomputed context doesn't cover (e.g. a different
    held item); the call must be recorded in AnalysisResult.agent_tool_calls
    so the UI can flag it (ADR-028)."""
    model = _fake_model_with_tool_call(
        selection_json=json.dumps(
            {"focus_species": ["Garchomp", "Sinistcha"],
             "matchups": [["Garchomp", "Sinistcha"]], "rationale": "lcel"}
        ),
        tool_name="damage_calc",
        tool_args={
            "attacker_species": "Garchomp", "defender_species": "Sinistcha",
            "move": "Earthquake", "attacker_item": "Life Orb",
        },
        final_answer="With Life Orb it would 2HKO.",
    )
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(
        AnalysisRequest(session_id="lc-4", replay_json=replay,
                        question="What if Garchomp had Life Orb?")
    )
    assert result.answer == "With Life Orb it would 2HKO."
    assert len(result.agent_tool_calls) == 1
    call = result.agent_tool_calls[0]
    assert call.tool == "damage_calc"
    assert call.ok is True
    assert call.arguments["attacker_item"] == "Life Orb"


def test_no_tool_call_means_empty_agent_tool_calls(sample_chaos_path, sample_replay_path):
    """The overwhelmingly common case — no follow-up needed — must leave
    agent_tool_calls empty, same as the native AnalysisService always does
    (it has no agent loop at all)."""
    orch = _build(sample_chaos_path, _fake_model("{}", "plain answer, no tools needed"))
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="lc-5", replay_json=replay, question="q"))
    assert result.agent_tool_calls == []


def test_failed_tool_call_degrades_instead_of_crashing_the_turn(
    sample_chaos_path, sample_replay_path
):
    """A tool-side domain exception (CalcEngineError) must degrade to
    {"ok": False, "error": ...} (langchain_tools.py) and surface as
    AgentToolInvocation(ok=False) — never crash the whole analyze() call,
    mirroring the same degrade convention already used at the Node IPC
    boundary."""
    model = _fake_model_with_tool_call(
        selection_json=json.dumps({"focus_species": ["Garchomp"], "matchups": [], "rationale": "r"}),
        tool_name="damage_calc",
        tool_args={
            "attacker_species": "Garchomp", "defender_species": "Sinistcha",
            "move": "Nonexistentmove",
        },
        final_answer="That move isn't recognized, so I can't say.",
    )
    orch = LangChainAnalysisOrchestrator(
        parser=ShowdownReplayParser(), chat_model=model,
        meta_provider=ChaosAdapter(sample_chaos_path),
        calc_engine=_SelectivelyFailingCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(sample_chaos_path),
        memory=InMemoryConversationMemory(), provider_name="langchain:fake",
    )
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="lc-6", replay_json=replay, question="q"))
    assert result.answer == "That move isn't recognized, so I can't say."
    assert len(result.agent_tool_calls) == 1
    assert result.agent_tool_calls[0].ok is False
    assert "Nonexistentmove" in result.agent_tool_calls[0].summary
