"""Minimal tests for the BYOK embedding provider adapters.

Only constructor validation is covered here — the underlying SDK calls need
real network/keys, same convention as the two chat LLM providers
(OpenAIProvider/GeminiProvider), which aren't unit-tested for that same
reason. Retrieval behavior itself is covered with a fake in
test_semantic_strategy_retriever.py, matching this project's established
"fakes for logic, live-skip for real transport" split.
"""

from __future__ import annotations

import pytest

from src.adapters.llm.gemini_embedding_provider import GeminiEmbeddingProvider
from src.adapters.llm.openai_embedding_provider import OpenAIEmbeddingProvider
from src.domain.exceptions import ConfigurationError


def test_openai_embedding_provider_requires_key():
    with pytest.raises(ConfigurationError):
        OpenAIEmbeddingProvider(api_key=None)


def test_gemini_embedding_provider_requires_key():
    with pytest.raises(ConfigurationError):
        GeminiEmbeddingProvider(api_key=None)
