"""Condition B: the ungrounded baseline.

Same replay, same question — but the LLM only ever sees the raw Showdown
log text. No GameState, no MatchupEvaluator, no Chaos stats, no
TurnReplaySimulator. This is the standard "LLM alone, no retrieval/tool
grounding" control used in RAG/faithfulness papers.
"""

from __future__ import annotations

from pathlib import Path

from src.domain.interfaces import LLMProvider
from src.domain.models import ChatMessage

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "naive_baseline_system.txt"
_NAIVE_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8")


def run_naive_baseline(llm: LLMProvider, raw_log: str, question: str) -> str:
    user_turn = (
        f"Raw battle log:\n{raw_log}\n\n"
        f"User question: {question}\n\n"
        "Write your analysis."
    )
    return llm.complete(
        system=_NAIVE_SYSTEM,
        messages=[ChatMessage(role="user", content=user_turn)],
        temperature=0.3,
        json_mode=False,
    )
