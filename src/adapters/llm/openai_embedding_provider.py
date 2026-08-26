"""OpenAI embedding provider adapter (bring-your-own-key).

Used only by :class:`~src.adapters.smogon.semantic_strategy_retriever.
SemanticStrategyRetriever` to rank Smogon analysis passages against the
user's question — reuses the same OpenAI key the user already provided for
chat completions (``PROFESSORVGC_OPENAI_API_KEY``), no separate credential.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.exceptions import ConfigurationError, LLMProviderError


class OpenAIEmbeddingProvider:
    """:class:`~src.domain.interfaces.EmbeddingProvider` backed by OpenAI."""

    name = "openai"

    def __init__(self, api_key: str | None, model: str = "text-embedding-3-small") -> None:
        if not api_key:
            raise ConfigurationError("OpenAI API key is required (PROFESSORVGC_OPENAI_API_KEY)")
        self._model = model
        try:
            from openai import OpenAI  # noqa: PLC0415 - lazy optional dependency
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'openai' package is not installed. Run: pip install openai"
            ) from exc
        self._client = OpenAI(api_key=api_key)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(model=self._model, input=list(texts))
        except Exception as exc:  # noqa: BLE001 - SDK raises many concrete types
            raise LLMProviderError(f"OpenAI embedding request failed: {exc}") from exc
        # The API guarantees response.data is returned in the same order as
        # the input list — no re-sorting needed.
        return [item.embedding for item in response.data]
