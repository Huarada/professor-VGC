"""Tests for SemanticStrategyRetriever (no network; fake IPC + fake embeddings).

Uses the same FakeIpc pattern as test_smogon_dex.py so SmogonDexAdapter's own
IPC boundary stays real (only the transport is faked), and a small
deterministic FakeEmbeddingProvider (bag-of-words over a fixed vocabulary)
instead of a real embedding model — enough to prove real ranking behavior
(a passage that actually mentions "trick room" outranks one that doesn't)
without needing a real model or network.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from src.adapters.smogon.semantic_strategy_retriever import SemanticStrategyRetriever
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter
from src.domain.exceptions import LLMProviderError, StrategyKnowledgeError


class FakeIpc:
    def __init__(self, responses: dict):
        self._responses = responses

    def request(self, payload):
        return self._responses.get(payload["cmd"], {"ok": False, "error": "no data"})

    def close(self):  # pragma: no cover
        pass


_VOCAB = ("trick room", "scarf", "sweeper", "screens")


class FakeEmbeddingProvider:
    """Deterministic bag-of-words 'embedding' over a small fixed vocabulary
    — real cosine-similarity ranking behavior without a real model."""

    def __init__(self) -> None:
        self.calls = 0
        self.texts_embedded: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts_embedded.extend(texts)
        return [
            [float(text.lower().count(word)) for word in _VOCAB] for text in texts
        ]


class FailingEmbeddingProvider:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise LLMProviderError("embedding backend unavailable")


_MULTI_FORMAT_ANALYSES = {
    "ok": True,
    "result": [
        {
            "format": "gen9vgc2024rega",
            "overview": "A fast Scarf sweeper that outpaces most of the format.",
            "comments": "Runs Choice Scarf for maximum speed.",
            "sets": [
                {
                    "name": "Scarf Sweeper", "ability": "Rough Skin", "item": "Choice Scarf",
                    "moves": ["Earthquake", "Dragon Claw"],
                    "description": "A fast scarf sweeper set.",
                },
            ],
        },
        {
            "format": "gen9vgc2024regb",
            "overview": "Can also play a slower support role.",
            "comments": "",
            "sets": [
                {
                    "name": "Trick Room Support", "ability": "Sand Veil", "item": "Wide Lens",
                    "moves": ["Stealth Rock", "Protect"],
                    "description": "A slow trick room support set that raises screens.",
                },
            ],
        },
    ],
}

_NO_PROSE_ANALYSES = {
    "ok": True,
    "result": [
        {"format": "gen9vgc2024rega", "overview": "", "comments": "", "sets": [
            {"name": "Set", "ability": "Rough Skin", "item": "Life Orb",
             "moves": ["Earthquake"], "description": ""},
        ]},
    ],
}


def _dex(**responses) -> SmogonDexAdapter:
    return SmogonDexAdapter(ipc=FakeIpc(responses))


def test_no_question_falls_back_to_dex_default():
    dex = _dex(analyses=_MULTI_FORMAT_ANALYSES)
    embeddings = FakeEmbeddingProvider()
    retriever = SemanticStrategyRetriever(dex, embeddings)

    direct = dex.get_strategy("Garchomp")
    via_retriever = retriever.get_strategy("Garchomp")

    assert via_retriever.overview == direct.overview
    assert via_retriever.retrieval_note == ""
    assert embeddings.calls == 0  # never touched the embedding path at all


def test_question_ranks_the_relevant_passage_first():
    dex = _dex(analyses=_MULTI_FORMAT_ANALYSES)
    embeddings = FakeEmbeddingProvider()
    retriever = SemanticStrategyRetriever(dex, embeddings, top_k=1)

    strat = retriever.get_strategy("Garchomp", question="How does it perform in Trick Room?")

    assert "Trick Room Support" in strat.overview
    assert "Scarf" not in strat.overview  # top_k=1 excludes the unrelated passage
    assert strat.retrieval_note and "1/4" in strat.retrieval_note


def test_common_sets_aggregate_across_every_format_regardless_of_ranking():
    dex = _dex(analyses=_MULTI_FORMAT_ANALYSES)
    embeddings = FakeEmbeddingProvider()
    retriever = SemanticStrategyRetriever(dex, embeddings, top_k=1)

    strat = retriever.get_strategy("Garchomp", question="How does it perform in Trick Room?")

    joined = " ".join(strat.common_sets)
    assert "Scarf Sweeper" in joined and "Trick Room Support" in joined


def test_embedding_failure_degrades_to_dex_default():
    dex = _dex(analyses=_MULTI_FORMAT_ANALYSES)
    retriever = SemanticStrategyRetriever(dex, FailingEmbeddingProvider())

    strat = retriever.get_strategy("Garchomp", question="How does it perform in Trick Room?")

    assert strat.overview  # still got something usable
    assert strat.retrieval_note == ""  # the plain default path, not semantic


def test_no_prose_to_index_degrades_to_dex_default():
    dex = _dex(analyses=_NO_PROSE_ANALYSES)
    embeddings = FakeEmbeddingProvider()
    retriever = SemanticStrategyRetriever(dex, embeddings)

    strat = retriever.get_strategy("Garchomp", question="Anything to say?")

    assert strat.retrieval_note == ""
    assert "[Smogon official]" in strat.overview  # dex's own no-overview fallback text


def test_missing_analyses_raises_like_dex():
    dex = _dex()  # no responses -> primary always fails
    retriever = SemanticStrategyRetriever(dex, FakeEmbeddingProvider())

    with pytest.raises(StrategyKnowledgeError):
        retriever.get_strategy("Garchomp", question="anything")


def test_chunk_index_is_cached_across_questions_for_the_same_species():
    dex = _dex(analyses=_MULTI_FORMAT_ANALYSES)
    embeddings = FakeEmbeddingProvider()
    retriever = SemanticStrategyRetriever(dex, embeddings)

    retriever.get_strategy("Garchomp", question="How does it perform in Trick Room?")
    retriever.get_strategy("Garchomp", question="Is it a good scarf sweeper?")

    # 1 batched call to embed the 4 document chunks (cached after that) + 1
    # call per question asked (2) = 3 total .embed() calls, not 4.
    assert embeddings.calls == 3
