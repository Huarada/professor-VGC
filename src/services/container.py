"""Composition root — the ONLY place concrete adapters are instantiated."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.chaos_repository import ChaosRepository, ChaosRepositoryLike
from src.adapters.chaos.firestore_chaos_repository import FirestoreChaosRepository
from src.adapters.llm.adk_provider import build_adk_model
from src.adapters.llm.gemini_embedding_provider import GeminiEmbeddingProvider
from src.adapters.llm.gemini_provider import GeminiProvider
from src.adapters.llm.langchain_provider import build_chat_model
from src.adapters.llm.openai_embedding_provider import OpenAIEmbeddingProvider
from src.adapters.llm.openai_provider import OpenAIProvider
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.smogon.composite_strategy import CompositeStrategyProvider
from src.adapters.smogon.semantic_strategy_retriever import SemanticStrategyRetriever
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.config import Settings, load_settings
from src.domain.exceptions import ConfigurationError
from src.domain.interfaces import (
    AnalysisPipeline,
    ConversationMemory,
    EmbeddingProvider,
    LLMProvider,
    StrategyKnowledgeProvider,
)
from src.services.adk_orchestrator import AdkAnalysisOrchestrator
from src.services.analysis_service import AnalysisService
from src.services.langchain_orchestrator import LangChainAnalysisOrchestrator
from src.services.selection_service import LLMSelectionService

if TYPE_CHECKING:
    # Type-checking only — see langchain_provider.py's own note: every
    # runtime path to langchain_core/google-adk stays lazy, so this
    # composition root remains importable (and the native orchestration path
    # fully usable) without either installed.
    from google.adk.models import BaseLlm
    from langchain_core.language_models import BaseChatModel

_PROVIDERS = {"openai", "gemini"}
_ORCHESTRATORS = {"native", "langchain", "adk"}
_CHAOS_BACKENDS = {"local", "firestore"}


class Container:
    """Lazily wires and caches the object graph for one application instance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        self._memory: ConversationMemory | None = None
        self._calc: SmogonCalcAdapter | None = None
        self._dex: SmogonDexAdapter | None = None
        # Shared between chaos() and _chaos_strategy() — both need a
        # ChaosRepositoryLike, and building it twice would mean the
        # Firestore backend doubling every read for no reason (the local
        # backend already avoided this by being cheap to re-glob; Firestore
        # is not "free" the same way, so this cache matters more there).
        self._chaos_repo: ChaosRepositoryLike | None = None
        # Keyed by provider name ("openai"/"gemini") — a session may switch
        # provider mid-conversation via the sidebar, so this caches at most
        # one retriever per provider actually used, each with its own
        # internal per-species chunk cache (see SemanticStrategyRetriever).
        self._semantic_retrievers: dict[str, SemanticStrategyRetriever] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def memory(self) -> ConversationMemory:
        if self._memory is None:
            self._memory = InMemoryConversationMemory()
        return self._memory

    def calc_engine(self) -> SmogonCalcAdapter:
        if self._calc is None:
            self._calc = SmogonCalcAdapter(
                server_script=self._settings.calc_server_script,
                node_binary=self._settings.node_binary,
                gen=self._settings.calc_gen,
                timeout_seconds=self._settings.calc_timeout_seconds,
            )
        return self._calc

    def chaos_repository(self) -> ChaosRepositoryLike:
        """The shared Chaos data source — local files or Firestore, per
        PROFESSORVGC_CHAOS_BACKEND. Built once and cached: for the Firestore
        backend this is what makes chaos()/_chaos_strategy() sharing one
        instance actually save reads, not just avoid a redundant object."""
        if self._chaos_repo is None:
            backend = self._settings.chaos_backend.lower()
            if backend not in _CHAOS_BACKENDS:
                raise ConfigurationError(
                    f"Unknown chaos backend '{backend}'. Available: {sorted(_CHAOS_BACKENDS)}"
                )
            if backend == "firestore":
                self._chaos_repo = FirestoreChaosRepository(
                    self._settings.firestore_project_id or "",
                    database_id=self._settings.firestore_database_id,
                    collection=self._settings.firestore_chaos_collection,
                    credentials_path=self._settings.firestore_credentials_path,
                    reg_fallback_depth=self._settings.reg_fallback_depth,
                )
            else:
                self._chaos_repo = ChaosRepository(
                    self._settings.chaos_data_path,
                    reg_fallback_depth=self._settings.reg_fallback_depth,
                )
        return self._chaos_repo

    def chaos(self) -> ChaosAdapter:
        return ChaosAdapter(repository=self.chaos_repository(), top_n=self._settings.chaos_top_n)

    def _chaos_strategy(self) -> ChaosStrategyAdapter:
        return ChaosStrategyAdapter(repository=self.chaos_repository())

    def smogon_dex(self) -> SmogonDexAdapter | None:
        """Official @pkmn/smogon adapter (None when disabled)."""
        if not self._settings.use_smogon_dex:
            return None
        if self._dex is None:
            self._dex = SmogonDexAdapter(
                server_script=self._settings.smogon_dex_script,
                node_binary=self._settings.node_binary,
                gen=self._settings.calc_gen,
                timeout_seconds=self._settings.smogon_dex_timeout_seconds,
            )
        return self._dex

    def build_embedding_provider(self, provider: str | None = None) -> EmbeddingProvider:
        """Instantiate the requested BYOK provider for embeddings (same key
        as chat completions — see PROFESSORVGC_USE_SEMANTIC_STRATEGY)."""
        name = (provider or self._settings.default_provider).lower()
        if name not in _PROVIDERS:
            raise ConfigurationError(
                f"Unknown provider '{name}'. Available: {sorted(_PROVIDERS)}"
            )
        if name == "openai":
            return OpenAIEmbeddingProvider(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_embedding_model,
            )
        return GeminiEmbeddingProvider(
            api_key=self._settings.gemini_api_key,
            model=self._settings.gemini_embedding_model,
        )

    def _semantic_dex(
        self, provider: str | None, dex: SmogonDexAdapter
    ) -> StrategyKnowledgeProvider:
        """Wraps `dex` with question-aware semantic retrieval (ADR-027) when
        enabled; returns `dex` itself unchanged otherwise. Cached per
        provider name so a session's per-species chunk/embedding cache
        (inside SemanticStrategyRetriever) survives across turns instead of
        being rebuilt — and its cache wiped — on every `analyze()` call."""
        if not self._settings.use_semantic_strategy:
            return dex
        name = (provider or self._settings.default_provider).lower()
        cached = self._semantic_retrievers.get(name)
        if cached is None:
            cached = SemanticStrategyRetriever(
                dex,
                self.build_embedding_provider(name),
                top_k=self._settings.semantic_strategy_top_k,
            )
            self._semantic_retrievers[name] = cached
        return cached

    def strategy(self, provider: str | None = None) -> StrategyKnowledgeProvider:
        """Composite (official Smogon analyses -> Chaos fallback) or Chaos
        only. `provider` selects which BYOK key powers semantic retrieval
        (see PROFESSORVGC_USE_SEMANTIC_STRATEGY); irrelevant when that's off."""
        chaos = self._chaos_strategy()
        dex = self.smogon_dex()
        if dex is not None:
            primary = self._semantic_dex(provider, dex)
            return CompositeStrategyProvider(primary=primary, fallback=chaos)
        return chaos

    def build_llm(self, provider: str | None = None) -> LLMProvider:
        """Instantiate the requested BYOK provider (direct SDK, native path)."""
        name = (provider or self._settings.default_provider).lower()
        if name not in _PROVIDERS:
            raise ConfigurationError(
                f"Unknown provider '{name}'. Available: {sorted(_PROVIDERS)}"
            )
        if name == "openai":
            return OpenAIProvider(
                api_key=self._settings.openai_api_key, model=self._settings.openai_model
            )
        return GeminiProvider(
            api_key=self._settings.gemini_api_key, model=self._settings.gemini_model
        )

    def build_chat_model(self, provider: str | None = None) -> BaseChatModel:
        """Instantiate the requested BYOK provider as a LangChain chat model."""
        name = (provider or self._settings.default_provider).lower()
        return build_chat_model(name, self._settings)

    def build_adk_model(self, provider: str | None = None) -> "str | BaseLlm":
        """Instantiate the requested BYOK provider as a Google ADK model."""
        name = (provider or self._settings.default_provider).lower()
        return build_adk_model(name, self._settings)

    def build_native_pipeline(self, provider: str | None = None) -> AnalysisPipeline:
        """Assemble the hand-rolled :class:`AnalysisService`."""
        llm = self.build_llm(provider)
        return AnalysisService(
            parser=ShowdownReplayParser(),
            selector=LLMSelectionService(llm, temperature=0.0),
            meta_provider=self.chaos(),
            calc_engine=self.calc_engine(),
            strategy_provider=self.strategy(provider),
            llm=llm,
            memory=self.memory(),
            default_gen=self._settings.calc_gen,
            suggestion_source=self.smogon_dex(),
        )

    def build_langchain_pipeline(self, provider: str | None = None) -> AnalysisPipeline:
        """Assemble the LangChain LCEL orchestrator."""
        name = (provider or self._settings.default_provider).lower()
        chat_model = self.build_chat_model(name)
        return LangChainAnalysisOrchestrator(
            parser=ShowdownReplayParser(),
            chat_model=chat_model,
            meta_provider=self.chaos(),
            calc_engine=self.calc_engine(),
            strategy_provider=self.strategy(name),
            memory=self.memory(),
            default_gen=self._settings.calc_gen,
            provider_name=f"langchain:{name}",
            suggestion_source=self.smogon_dex(),
        )

    def build_adk_pipeline(self, provider: str | None = None) -> AnalysisPipeline:
        """Assemble the Google ADK orchestrator (default backend)."""
        name = (provider or self._settings.default_provider).lower()
        model = self.build_adk_model(name)
        return AdkAnalysisOrchestrator(
            parser=ShowdownReplayParser(),
            model=model,
            meta_provider=self.chaos(),
            calc_engine=self.calc_engine(),
            strategy_provider=self.strategy(name),
            memory=self.memory(),
            default_gen=self._settings.calc_gen,
            provider_name=f"adk:{name}",
            suggestion_source=self.smogon_dex(),
        )

    def build_pipeline(
        self, provider: str | None = None, orchestrator: str | None = None
    ) -> AnalysisPipeline:
        """Assemble the configured pipeline (orchestrator-agnostic entrypoint)."""
        backend = (orchestrator or self._settings.orchestrator).lower()
        if backend not in _ORCHESTRATORS:
            raise ConfigurationError(
                f"Unknown orchestrator '{backend}'. Available: {sorted(_ORCHESTRATORS)}"
            )
        if backend == "adk":
            return self.build_adk_pipeline(provider)
        if backend == "langchain":
            return self.build_langchain_pipeline(provider)
        return self.build_native_pipeline(provider)

    def build_analysis_service(self, provider: str | None = None) -> AnalysisPipeline:
        return self.build_native_pipeline(provider)

    def shutdown(self) -> None:
        """Release long-lived resources (Node subprocesses, Firestore gRPC channel)."""
        if self._calc is not None:
            self._calc.close()
            self._calc = None
        if self._dex is not None:
            self._dex.close()
            self._dex = None
        if self._chaos_repo is not None:
            # ChaosRepository (local files) has no close() at all — nothing
            # to release; FirestoreChaosRepository does (its gRPC channel).
            close = getattr(self._chaos_repo, "close", None)
            if close is not None:
                close()
            self._chaos_repo = None
