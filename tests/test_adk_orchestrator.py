"""End-to-end test of the Google ADK orchestrator (fake BaseLlm).

google-adk is a normal top-level dependency (pyproject.toml/requirements.txt,
and the DEFAULT orchestrator) — but this whole file's own module-level
imports pull in google.adk/google.genai directly, so a minimal/partial
install missing it would otherwise fail at COLLECTION time (an error, not a
skip) for every test here. Guarded with importorskip so that specific,
well-known case degrades to a clean skip instead, matching this project's own
test_langchain_orchestrator.py convention.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Iterable

import pytest

pytest.importorskip("google.adk")

from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.exceptions import CalcEngineError, LLMProviderError
from src.domain.interfaces import AnalysisPipeline
from src.domain.models import AnalysisRequest
from src.services.adk_orchestrator import AdkAnalysisOrchestrator
from tests.conftest import FakeCalcEngine

# Each scripted "turn" is either a plain string (a final text response) or a
# (tool_name, args_dict) pair (a function-call response, to exercise the
# explanation agent's tool loop) — mirrors test_langchain_orchestrator.py's
# own scripted-message shape.
_Turn = str | tuple[str, dict[str, Any]]


class _FakeAdkModel(BaseLlm):
    """Deterministic BaseLlm stand-in: yields one scripted response per
    `generate_content_async` call, consumed in order across BOTH the
    selection and explanation agents (they share one model instance, exactly
    like the real orchestrator wires one `model` into both `Agent`s)."""

    model: str = "fake-adk-model"
    _turns: Any = PrivateAttr()

    def __init__(self, turns: Iterable[_Turn], **data: Any) -> None:
        super().__init__(**data)
        self._turns = iter(turns)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        turn = next(self._turns)
        if isinstance(turn, tuple):
            name, args = turn
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text=turn)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class _RaisingFakeAdkModel(BaseLlm):
    """Stands in for a model whose underlying SDK call fails (rate limit,
    exhausted quota, auth, network, ...)."""

    model: str = "fake-adk-model-raising"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise RuntimeError("insufficient_quota: credit_balance_exhausted")
        yield  # pragma: no cover - unreachable; keeps this an async generator


class _HangingFakeAdkModel(BaseLlm):
    """Stands in for the live failure mode this project actually hit
    (2026-08-28 faithfulness benchmark, ADK+Gemini 3.5-flash): a turn that
    never raises and never returns within any reasonable time — NOT a
    retriable error (adk_provider.py's retry_options bound handles those),
    a stuck tool-calling loop. Sleeps far longer than any timeout a test
    below configures, so reaching the assertion at all proves the timeout
    actually cut the call off rather than the fake happening to finish
    fast enough on its own."""

    model: str = "fake-adk-model-hanging"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        await asyncio.sleep(30)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="too late")]))


class _HangsOnFirstCallOnlyAdkModel(BaseLlm):
    """Hangs (see _HangingFakeAdkModel) on exactly the FIRST turn (the
    selection agent's, since it always runs before the explanation agent —
    see analyze()), then answers normally on every subsequent turn —
    isolates "the selection stage's own timeout falls back correctly and
    the pipeline still completes" from "a hung explanation turn eventually
    raises too", which a model that hangs on every call can't distinguish."""

    model: str = "fake-adk-model-hangs-once"
    _remaining_answers: Any = PrivateAttr()
    _call_count: Any = PrivateAttr()

    def __init__(self, answers_after_hang: Iterable[_Turn], **data: Any) -> None:
        super().__init__(**data)
        self._remaining_answers = iter(answers_after_hang)
        self._call_count = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._call_count += 1
        if self._call_count == 1:
            await asyncio.sleep(30)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="too late")]))
            return
        turn = next(self._remaining_answers)
        part = types.Part(text=turn) if isinstance(turn, str) else types.Part(
            function_call=types.FunctionCall(name=turn[0], args=turn[1])
        )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def _build(chaos_path, model: BaseLlm, **kwargs: Any) -> AdkAnalysisOrchestrator:
    return AdkAnalysisOrchestrator(
        parser=ShowdownReplayParser(), model=model,
        meta_provider=ChaosAdapter(chaos_path), calc_engine=FakeCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(chaos_path),
        memory=InMemoryConversationMemory(), provider_name="adk:fake",
        **kwargs,
    )


def test_orchestrator_satisfies_pipeline_port(sample_chaos_path):
    assert isinstance(_build(sample_chaos_path, _FakeAdkModel(["{}", "x"])), AnalysisPipeline)


def test_full_adk_pipeline(sample_chaos_path, sample_replay_path):
    model = _FakeAdkModel(
        [
            json.dumps(
                {"focus_species": ["Garchomp", "Sinistcha"],
                 "matchups": [["Garchomp", "Sinistcha"]], "rationale": "adk"}
            ),
            "Garchomp threatens a 2HKO; Sinistcha is the safe pivot.",
        ]
    )
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(
        AnalysisRequest(session_id="adk-1", replay_json=replay,
                        question="Does Garchomp beat Sinistcha?")
    )
    assert result.provider == "adk:fake"
    assert result.answer.startswith("Garchomp")
    assert result.verdicts
    v = result.verdicts[0]
    assert v.attacker == "Garchomp" and v.defender == "Sinistcha"
    assert "Garchomp" in result.meta_context.pokemon_stats


def test_unparseable_selection_falls_back(sample_chaos_path, sample_replay_path):
    orch = _build(sample_chaos_path, _FakeAdkModel(["not json", "ok"]))
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="adk-2", replay_json=replay, question="q"))
    assert result.verdicts


def test_explanation_turn_timeout_raises_llm_provider_error(
    sample_chaos_path, sample_replay_path
):
    """Port of the wall-clock cutoff that shielded today's faithfulness
    benchmark (scripts/faithfulness_benchmark/run.py's
    _FIXTURE_WALL_CLOCK_TIMEOUT_SECONDS) into the actual production
    pipeline: a stuck explanation turn must surface as the same typed
    LLMProviderError a Streamlit session already knows how to render,
    within a bounded time — not hang the request indefinitely."""
    model = _HangingFakeAdkModel()
    orch = _build(sample_chaos_path, model, agent_timeout_seconds=0.05)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    with pytest.raises(LLMProviderError, match="exceeded its"):
        orch.analyze(AnalysisRequest(session_id="adk-timeout-1", replay_json=replay, question="q"))


def test_selection_turn_timeout_falls_back_like_any_other_failure(
    sample_chaos_path, sample_replay_path
):
    """The selection stage already tolerates any model failure by falling
    back to the deterministic species list (test_unparseable_selection_
    falls_back exercises the same fallback for bad JSON) — a timeout must
    take that same path, not a special one: the pipeline should still
    complete normally once the (fast, non-hanging) explanation turn runs,
    proving _select()'s own try/except actually swallows the timeout
    rather than letting it propagate uncaught."""
    model = _HangsOnFirstCallOnlyAdkModel(["plain answer, no tools needed"])
    orch = _build(sample_chaos_path, model, agent_timeout_seconds=0.05)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="adk-timeout-2", replay_json=replay, question="q"))
    assert result.answer == "plain answer, no tools needed"
    assert result.verdicts  # deterministic fallback still picked candidate species


def test_explanation_empty_final_text_raises_instead_of_blank_answer(
    sample_chaos_path, sample_replay_path
):
    """Live bug (2026-08-29): the explanation agent's final event
    sometimes carries no usable text (empty/whitespace parts, or a
    non-text part) — is_final_response() is still True (no exception,
    no timeout: `_run_agent`'s loop just ends normally), but `_final_text`
    resolves to "". Before this fix, AnalysisResult.answer == "" reached
    the UI with NO error at all — a blank response with no indication
    anything had gone wrong. Confirmed NOT to be RunConfig.max_llm_calls
    exhaustion (that raises google.adk's own LlmCallsLimitExceededError,
    already correctly wrapped by analyze()'s existing except clause) —
    this is a distinct failure mode this test isolates directly by
    scripting the final turn as an empty string."""
    model = _FakeAdkModel([
        json.dumps({"focus_species": ["Garchomp"], "matchups": [], "rationale": "r"}),
        "",  # explanation's "final" turn carries no text
    ])
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    with pytest.raises(LLMProviderError, match="without producing a final answer"):
        orch.analyze(AnalysisRequest(session_id="adk-empty-1", replay_json=replay, question="q"))


def test_selection_empty_final_text_falls_back_like_any_other_failure(
    sample_chaos_path, sample_replay_path
):
    """Same failure mode as the test above, but on the selection stage —
    must take the existing any-failure-falls-back-to-deterministic-species
    path, not a special one: the pipeline should still complete normally
    once the (fast, real-text) explanation turn runs, proving _select()'s
    own try/except actually swallows this new error rather than letting
    it propagate uncaught."""
    model = _FakeAdkModel([
        "",  # selection's "final" turn carries no text
        "plain answer, no tools needed",
    ])
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="adk-empty-2", replay_json=replay, question="q"))
    assert result.answer == "plain answer, no tools needed"
    assert result.verdicts  # deterministic fallback still picked candidate species


def test_explanation_provider_failure_is_wrapped_not_left_raw(
    sample_chaos_path, sample_replay_path
):
    """Reported (LangChain equivalent: ADR-011): a provider failure must
    reach the UI as the typed LLMProviderError the presentation layer
    already knows how to render, never a raw SDK exception — the ADK
    backend talks to the raw model object directly, same as LangChain's LCEL
    chains do, so it needs the identical wrapping."""
    orch = _build(sample_chaos_path, _RaisingFakeAdkModel())
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    with pytest.raises(LLMProviderError, match="explanation model call failed"):
        orch.analyze(AnalysisRequest(session_id="adk-3", replay_json=replay, question="q"))


# --- agentic follow-up (explanation stage as a bounded tool-calling agent,
# ADK equivalent of ADR-028) ------------------------------------------------


class _SelectivelyFailingCalcEngine(FakeCalcEngine):
    """FakeCalcEngine that raises CalcEngineError for one sentinel move name,
    so a scripted tool call can exercise the {"ok": False, "error": ...}
    degrade path (adk_tools.py) without disturbing the deterministic
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
    so the UI can flag it."""
    model = _FakeAdkModel(
        [
            json.dumps(
                {"focus_species": ["Garchomp", "Sinistcha"],
                 "matchups": [["Garchomp", "Sinistcha"]], "rationale": "adk"}
            ),
            (
                "damage_calc",
                {
                    "attacker_species": "Garchomp", "defender_species": "Sinistcha",
                    "move": "Earthquake", "attacker_item": "Life Orb",
                    "attacker_nature": "",
                },
            ),
            "With Life Orb it would 2HKO.",
        ]
    )
    orch = _build(sample_chaos_path, model)
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(
        AnalysisRequest(session_id="adk-4", replay_json=replay,
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
    orch = _build(sample_chaos_path, _FakeAdkModel(["{}", "plain answer, no tools needed"]))
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="adk-5", replay_json=replay, question="q"))
    assert result.agent_tool_calls == []


def test_failed_tool_call_degrades_instead_of_crashing_the_turn(
    sample_chaos_path, sample_replay_path
):
    """A tool-side domain exception (CalcEngineError) must degrade to
    {"ok": False, "error": ...} (adk_tools.py) and surface as
    AgentToolInvocation(ok=False) — never crash the whole analyze() call,
    mirroring the same degrade convention already used at the Node IPC
    boundary and by the LangChain backend."""
    model = _FakeAdkModel(
        [
            json.dumps({"focus_species": ["Garchomp"], "matchups": [], "rationale": "r"}),
            (
                "damage_calc",
                {
                    "attacker_species": "Garchomp", "defender_species": "Sinistcha",
                    "move": "Nonexistentmove", "attacker_item": "", "attacker_nature": "",
                },
            ),
            "That move isn't recognized, so I can't say.",
        ]
    )
    orch = AdkAnalysisOrchestrator(
        parser=ShowdownReplayParser(), model=model,
        meta_provider=ChaosAdapter(sample_chaos_path),
        calc_engine=_SelectivelyFailingCalcEngine(),
        strategy_provider=ChaosStrategyAdapter(sample_chaos_path),
        memory=InMemoryConversationMemory(), provider_name="adk:fake",
    )
    replay = json.loads(sample_replay_path.read_text(encoding="utf-8"))
    result = orch.analyze(AnalysisRequest(session_id="adk-6", replay_json=replay, question="q"))
    assert result.answer == "That move isn't recognized, so I can't say."
    assert len(result.agent_tool_calls) == 1
    assert result.agent_tool_calls[0].ok is False
    assert "Nonexistentmove" in result.agent_tool_calls[0].summary
