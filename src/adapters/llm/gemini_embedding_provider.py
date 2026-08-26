"""Google Gemini embedding provider adapter (bring-your-own-key).

Used only by :class:`~src.adapters.smogon.semantic_strategy_retriever.
SemanticStrategyRetriever` to rank Smogon analysis passages against the
user's question — reuses the same Gemini key the user already provided for
chat completions (``PROFESSORVGC_GEMINI_API_KEY``), no separate credential.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.domain.exceptions import ConfigurationError, LLMProviderError


class GeminiEmbeddingProvider:
    """:class:`~src.domain.interfaces.EmbeddingProvider` backed by Gemini."""

    name = "gemini"

    def __init__(self, api_key: str | None, model: str = "models/text-embedding-004") -> None:
        if not api_key:
            raise ConfigurationError("Gemini API key is required (PROFESSORVGC_GEMINI_API_KEY)")
        self._model = model
        try:
            import google.generativeai as genai  # noqa: PLC0415 - lazy optional dep
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'google-generativeai' package is not installed. "
                "Run: pip install google-generativeai"
            ) from exc
        # See gemini_provider.py's identical note: `configure` is real,
        # documented, stable public API that this package's stubs just don't
        # re-export from the package root.
        genai.configure(api_key=api_key)  # type: ignore[attr-defined]
        self._genai = genai

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            # embed_content accepts either a single string or a list; given a
            # list it returns {"embedding": [[...], [...], ...]} in the same
            # order — same stub-completeness gap as `configure` above.
            response: Any = self._genai.embed_content(  # type: ignore[attr-defined]
                model=self._model, content=list(texts), task_type="retrieval_document",
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises many concrete types
            raise LLMProviderError(f"Gemini embedding request failed: {exc}") from exc
        embeddings = response.get("embedding") if isinstance(response, dict) else None
        if not embeddings:
            raise LLMProviderError("Gemini returned no embeddings")
        return [list(vec) for vec in embeddings]
