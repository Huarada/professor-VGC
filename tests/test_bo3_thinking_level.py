"""BO3 Gemini depth is explicit in every provider path; BO1 has no override."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types

from src.adapters.llm.gemini_provider import GeminiProvider
from src.adapters.llm.langchain_provider import build_chat_model
from src.config import Settings
from src.domain.models import ChatMessage


@pytest.mark.parametrize("level,expected", [
    ("low", types.ThinkingLevel.LOW),
    ("medium", types.ThinkingLevel.MEDIUM),
])
def test_direct_gemini_summary_sends_selected_thinking_level(level, expected):
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._model_name = "gemini-3.5-flash"
    provider._timeout_seconds = 90.0
    provider._total_timeout_seconds = 180.0
    provider._thinking_level = level
    generate = Mock(return_value=SimpleNamespace(text="Evidence-based summary"))
    provider._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    result = provider.complete(system="Review", messages=[ChatMessage(role="user", content="Game evidence")])

    assert result == "Evidence-based summary"
    config = generate.call_args.kwargs["config"]
    assert config.thinking_config.thinking_level == expected
    assert generate.call_args.kwargs["contents"][0].parts[0].text == "Game evidence"


@pytest.mark.parametrize("level", ["low", "medium"])
def test_langchain_gemini_model_receives_selected_level(level):
    settings = Settings(_env_file=None, gemini_api_key="test-only")
    model = build_chat_model("gemini", settings, thinking_level=level)
    assert model.thinking_level == level


def test_bo1_langchain_model_keeps_provider_default_depth():
    settings = Settings(_env_file=None, gemini_api_key="test-only")
    assert build_chat_model("gemini", settings).thinking_level is None
