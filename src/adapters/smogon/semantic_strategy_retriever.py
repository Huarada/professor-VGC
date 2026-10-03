"""Semantic retrieval over official Smogon analysis prose (ADR-027).

Wraps ``SmogonDexAdapter``, which only reads analysis [0]. This chunks every
format's overview and every set's description, embeds them with the
question, and keeps the closest passages by cosine similarity. In-memory
(a few paragraphs per species need no vector database). Structured fields
are aggregated from all formats regardless of the question. Without a
question, or on any embedding failure, it falls back to the wrapped adapter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from src.adapters.smogon.archetype_signals import infer_archetypes
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter, describe_smogon_set
from src.domain.exceptions import ConfigurationError, LLMProviderError, StrategyKnowledgeError
from src.domain.interfaces import EmbeddingProvider
from src.domain.models import SmogonStrategy


@dataclass(frozen=True)
class _Chunk:
    """One retrievable passage of official Smogon prose."""

    text: str
    label: str  # e.g. "gen9vgc2025regh" or 'gen9vgc2025regh / set "Fast Support"'
    vector: list[float]


@dataclass(frozen=True)
class _SpeciesIndex:
    """Everything indexed for one (species, metagame) — cached, since the
    underlying analyses don't change while this process is running."""

    analyses: list[dict[str, Any]]  # raw, for structured-field aggregation
    chunks: list[_Chunk]  # embedded prose, for ranking


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain cosine similarity — no numpy needed for corpora this small."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticStrategyRetriever:
    """:class:`~src.domain.interfaces.StrategyKnowledgeProvider` decorator
    adding question-aware retrieval on top of a `SmogonDexAdapter`."""

    def __init__(
        self,
        dex: SmogonDexAdapter,
        embeddings: EmbeddingProvider,
        *,
        top_k: int = 3,
        top_n: int = 4,
    ) -> None:
        self._dex = dex
        self._embeddings = embeddings
        self._top_k = max(1, int(top_k))
        self._top_n = max(1, int(top_n))
        # "species|metagame" -> chunks; lives as long as the per-session retriever.
        self._cache: dict[str, _SpeciesIndex] = {}

    def get_strategy(
        self, species: str, *, metagame: str | None = None, question: str | None = None
    ) -> SmogonStrategy:
        if not question or not question.strip():
            # Nothing to rank passages against — retrieval has nothing to
            # add over the plain default.
            return self._dex.get_strategy(species, metagame=metagame, question=question)
        try:
            return self._semantic_strategy(species, metagame, question)
        except (StrategyKnowledgeError, LLMProviderError, ConfigurationError):
            # Any embedding failure falls back to the wrapped adapter's plain behavior.
            return self._dex.get_strategy(species, metagame=metagame, question=question)

    # -- internals -------------------------------------------------------- #

    def _semantic_strategy(
        self, species: str, metagame: str | None, question: str
    ) -> SmogonStrategy:
        index = self._index_for(species, metagame)
        if not index.chunks:
            raise StrategyKnowledgeError(f"No Smogon prose to retrieve for {species}")
        [question_vector] = self._embeddings.embed([question])
        ranked = sorted(
            index.chunks, key=lambda c: _cosine(c.vector, question_vector), reverse=True
        )
        top = ranked[: self._top_k]
        overview = " ".join(f"[Smogon official: {c.label}] {c.text}" for c in top)
        common_sets, moves, abilities = self._aggregate_sets(index.analyses)
        return SmogonStrategy(
            species=species,
            overview=overview,
            common_sets=common_sets,
            common_teammates=[],
            archetypes=infer_archetypes(moves, abilities),
            retrieval_note=(
                f"semantic retrieval: {len(top)}/{len(index.chunks)} passage(s) "
                f"across {len(index.analyses)} format(s)"
            ),
        )

    def _index_for(self, species: str, metagame: str | None) -> _SpeciesIndex:
        cache_key = f"{species}|{metagame or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        analyses = self._dex.get_analyses_raw(species, metagame=metagame)
        if not analyses:
            raise StrategyKnowledgeError(f"No Smogon analyses for {species}")

        sources: list[tuple[str, str]] = []  # (text, label)
        for analysis in analyses:
            fmt = str(analysis.get("format") or "?")
            overview_text = " ".join(
                part for part in (analysis.get("overview"), analysis.get("comments")) if part
            ).strip()
            if overview_text:
                sources.append((overview_text, fmt))
            for s in analysis.get("sets", []) or []:
                desc = str(s.get("description") or "").strip()
                if desc:
                    sources.append((desc, f'{fmt} / set "{s.get("name", "Set")}"'))

        chunks: list[_Chunk] = []
        if sources:
            vectors = self._embeddings.embed([text for text, _ in sources])
            chunks = [
                _Chunk(text=text, label=label, vector=vector)
                for (text, label), vector in zip(sources, vectors)
            ]

        index = _SpeciesIndex(analyses=analyses, chunks=chunks)
        self._cache[cache_key] = index
        return index

    def _aggregate_sets(
        self, analyses: list[dict[str, Any]]
    ) -> tuple[list[str], list[str], list[str]]:
        """Common sets/moves/abilities from every format's sets, not just [0]."""
        common_sets: list[str] = []
        moves: list[str] = []
        abilities: list[str] = []
        for analysis in analyses:
            for s in analysis.get("sets", []) or []:
                if len(common_sets) < self._top_n:
                    common_sets.append(describe_smogon_set(s))
                for mv in s.get("moves", []) or []:
                    name = mv if isinstance(mv, str) else (mv[0] if mv else "")
                    if name and name not in moves:
                        moves.append(name)
                ability_field = s.get("ability")
                ability_name = (
                    ability_field[0] if isinstance(ability_field, list) and ability_field
                    else ability_field if isinstance(ability_field, str)
                    else ""
                )
                if ability_name and ability_name not in abilities:
                    abilities.append(ability_name)
        return common_sets, moves, abilities
