"""Tests for the LangChain LLM provider adapter (uses a fake chat model).

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

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from src.adapters.llm.langchain_provider import LangChainLLMProvider, to_lc_messages  # noqa: E402
from src.domain.interfaces import LLMProvider  # noqa: E402
from src.domain.models import ChatMessage  # noqa: E402


def test_provider_satisfies_protocol():
    model = GenericFakeChatModel(messages=iter([AIMessage(content="hi")]))
    assert isinstance(LangChainLLMProvider(model, name="fake"), LLMProvider)


def test_complete_returns_content():
    model = GenericFakeChatModel(messages=iter([AIMessage(content="the answer")]))
    out = LangChainLLMProvider(model, name="fake").complete(
        system="be helpful", messages=[ChatMessage(role="user", content="q")]
    )
    assert out == "the answer"


def test_message_mapping_roles():
    mapped = to_lc_messages(
        [
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
            ChatMessage(role="system", content="s"),
        ]
    )
    assert [type(m).__name__ for m in mapped] == [
        "HumanMessage", "AIMessage", "SystemMessage",
    ]
