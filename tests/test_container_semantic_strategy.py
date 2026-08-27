"""Wiring tests for PROFESSORVGC_USE_SEMANTIC_STRATEGY through the Container.

Construction-only (no real Node subprocess or network call happens just by
building these adapters) — verifies the composition root actually assembles
a SemanticStrategyRetriever when both flags are on, a plain SmogonDexAdapter
when the semantic flag is off, and plain Chaos when the dex itself is off —
without duplicating SemanticStrategyRetriever's own behavior tests (see
test_semantic_strategy_retriever.py).
"""

from __future__ import annotations

from src.adapters.smogon.composite_strategy import CompositeStrategyProvider
from src.adapters.smogon.semantic_strategy_retriever import SemanticStrategyRetriever
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.config import Settings
from src.services.container import Container


def _settings(**overrides) -> Settings:
    base = {
        "use_smogon_dex": True,
        "use_semantic_strategy": True,
        "openai_api_key": "test-key",
        "gemini_api_key": "test-key",
    }
    base.update(overrides)
    # _env_file=None: keeps this suite hermetic regardless of what a
    # contributor's own real .env sets (e.g. PROFESSORVGC_CHAOS_BACKEND=
    # firestore would otherwise make container.strategy() below silently
    # attempt a real Firestore connection via _chaos_strategy()'s fallback).
    return Settings(_env_file=None, **base)


def test_semantic_strategy_enabled_wraps_dex_with_retriever():
    container = Container(_settings())
    provider = container.strategy("openai")
    assert isinstance(provider, CompositeStrategyProvider)
    assert isinstance(provider._primary, SemanticStrategyRetriever)


def test_semantic_strategy_disabled_uses_plain_dex():
    container = Container(_settings(use_semantic_strategy=False))
    provider = container.strategy("openai")
    assert isinstance(provider, CompositeStrategyProvider)
    assert isinstance(provider._primary, SmogonDexAdapter)


def test_dex_disabled_uses_chaos_only_regardless_of_semantic_flag():
    container = Container(_settings(use_smogon_dex=False))
    provider = container.strategy("openai")
    assert isinstance(provider, ChaosStrategyAdapter)


def test_semantic_retriever_is_cached_per_provider():
    container = Container(_settings())
    first = container.strategy("openai")
    second = container.strategy("openai")
    assert isinstance(first, CompositeStrategyProvider)
    assert isinstance(second, CompositeStrategyProvider)
    assert first._primary is second._primary  # same cached retriever instance
