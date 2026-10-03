"""Composition root — the ONLY place concrete adapters are instantiated."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.adapters.calc.smogon_calc_adapter import SmogonCalcAdapter
from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.chaos_repository import ChaosRepositoryLike, StrictRegulationRepository
from src.adapters.chaos.firestore_chaos_repository import FirestoreChaosRepository
from src.adapters.firestore_client import build_firestore_client
from src.adapters.llm.adk_provider import build_adk_model
from src.adapters.llm.adk_tools import build_adk_tools
from src.adapters.llm.evidence_tools import EvidenceTools
from src.adapters.llm.gemini_embedding_provider import GeminiEmbeddingProvider
from src.adapters.llm.gemini_provider import GeminiProvider
from src.adapters.llm.langchain_provider import build_chat_model
from src.adapters.llm.langchain_tools import build_langchain_tools
from src.adapters.llm.openai_embedding_provider import OpenAIEmbeddingProvider
from src.adapters.llm.openai_provider import OpenAIProvider
from src.adapters.llm.prompts import FilePromptRepository
from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.adapters.parsers.showdown_parser import ShowdownReplayParser, parse_replay_for_viewer
from src.adapters.replay_url_fetcher import fetch_replay_json, normalize_replay_json_url
from src.adapters.smogon.composite_strategy import CompositeStrategyProvider
from src.adapters.smogon.semantic_strategy_retriever import SemanticStrategyRetriever
from src.adapters.smogon.smogon_dex_adapter import SmogonDexAdapter
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.adapters.usage.firestore_usage_quota import FirestoreUsageQuotaStore
from src.config import Settings, load_settings
from src.domain.exceptions import ConfigurationError
from src.domain.interfaces import (
    AnalysisPipeline,
    ConversationMemory,
    EmbeddingProvider,
    LLMProvider,
    PromptRepository,
    StrategyKnowledgeProvider,
)
from src.domain.regulation import Regulation, RegulationScope, parse_regulation_setting
from src.domain.replay_view_models import BattleReplay
from src.services.adk_orchestrator import AdkAnalysisOrchestrator
from src.services.analysis_service import AnalysisService
from src.services.ground_truth import GroundTruthAssembler
from src.services.langchain_orchestrator import LangChainAnalysisOrchestrator
from src.services.regulation_guard import RegulationGuard
from src.services.selection_service import LLMSelectionService
from src.services.usage_quota import UsageQuotaService

if TYPE_CHECKING:
    # langchain_core / google-adk stay lazy: the native path works without them.
    from google.adk.models import BaseLlm
    from langchain_core.language_models import BaseChatModel

_PROVIDERS = {"openai", "gemini"}
_ORCHESTRATORS = {"native", "langchain", "adk"}


class Container:
    """Lazily wires and caches the object graph for one application instance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        self._memory: ConversationMemory | None = None
        self._calc: SmogonCalcAdapter | None = None
        self._dex: SmogonDexAdapter | None = None
        # One repository shared by chaos() and _chaos_strategy(), so reads aren't doubled.
        self._chaos_repo: FirestoreChaosRepository | None = None
        # One retriever per provider used, keeping its per-species embedding cache.
        self._semantic_retrievers: dict[str, SemanticStrategyRetriever] = {}
        self._usage_quota: UsageQuotaService | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    def _resolve_provider(self, provider: str | None) -> str:
        """The requested BYOK provider name (default from settings), validated."""
        name = (provider or self._settings.default_provider).lower()
        if name not in _PROVIDERS:
            raise ConfigurationError(
                f"Unknown provider '{name}'. Available: {sorted(_PROVIDERS)}"
            )
        return name

    def prompts(self) -> PromptRepository:
        return FilePromptRepository()

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
        """The Chaos data source: Firestore, always (no local fallback; DATA.md).
        Built once and shared by the Chaos adapters.
        """
        if self._chaos_repo is None:
            self._chaos_repo = FirestoreChaosRepository(
                self._settings.firestore_project_id or "",
                database_id=self._settings.firestore_database_id,
                collection=self._settings.firestore_chaos_collection,
                credentials_path=self._settings.firestore_credentials_path,
                grpc_ca_bundle_path=self._settings.firestore_grpc_ca_bundle_path,
                reg_fallback_depth=self._settings.reg_fallback_depth,
            )
        return self._chaos_repo

    def _scoped_repository(self, strict: bool) -> ChaosRepositoryLike:
        repository = self.chaos_repository()
        return StrictRegulationRepository(repository) if strict else repository

    def chaos(self, *, strict: bool = False) -> ChaosAdapter:
        """Chaos usage stats; ``strict`` = never leave the requested regulation
        (no fallback to other regulations), used when the controller pins one."""
        return ChaosAdapter(
            repository=self._scoped_repository(strict), top_n=self._settings.chaos_top_n
        )

    def _chaos_strategy(self, *, strict: bool = False) -> ChaosStrategyAdapter:
        return ChaosStrategyAdapter(repository=self._scoped_repository(strict))

    def regulation(self, value: str | None = None) -> Regulation | None:
        """The pinned regulation for ``value`` (default: the configured
        controller), or None for "auto"."""
        return parse_regulation_setting(
            value or self._settings.regulation, self._settings.regulation_format_prefix
        )

    def regulation_choices(self) -> dict[str, str]:
        """Controller options for the UI: value -> label."""
        prefix = self._settings.regulation_format_prefix
        choices = {"auto": "Auto (the replay's regulation)"}
        for code in ("mb", "mc"):
            regulation = parse_regulation_setting(code, prefix)
            if regulation is not None:
                choices[code] = f"{regulation.label} only"
        return choices

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
        name = self._resolve_provider(provider)
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
        """``dex`` wrapped with semantic retrieval when enabled (ADR-027), cached per
        provider so its embedding cache survives across turns.
        """
        if not self._settings.use_semantic_strategy:
            return dex
        name = self._resolve_provider(provider)
        cached = self._semantic_retrievers.get(name)
        if cached is None:
            cached = SemanticStrategyRetriever(
                dex,
                self.build_embedding_provider(name),
                top_k=self._settings.semantic_strategy_top_k,
            )
            self._semantic_retrievers[name] = cached
        return cached

    def strategy(
        self, provider: str | None = None, *, strict: bool = False
    ) -> StrategyKnowledgeProvider:
        """Official Smogon analyses with Chaos fallback, or Chaos only; ``provider``
        picks the key for semantic retrieval.
        """
        chaos = self._chaos_strategy(strict=strict)
        dex = self.smogon_dex()
        if dex is not None:
            primary = self._semantic_dex(provider, dex)
            return CompositeStrategyProvider(primary=primary, fallback=chaos)
        return chaos

    def build_llm(self, provider: str | None = None) -> LLMProvider:
        """Instantiate the requested BYOK provider (direct SDK, native path)."""
        name = self._resolve_provider(provider)
        if name == "openai":
            return OpenAIProvider(
                api_key=self._settings.openai_api_key, model=self._settings.openai_model
            )
        return GeminiProvider(
            api_key=self._settings.gemini_api_key, model=self._settings.gemini_model
        )

    def build_chat_model(self, provider: str | None = None) -> BaseChatModel:
        """Instantiate the requested BYOK provider as a LangChain chat model."""
        return build_chat_model(self._resolve_provider(provider), self._settings)

    def build_adk_model(self, provider: str | None = None) -> "str | BaseLlm":
        """Instantiate the requested BYOK provider as a Google ADK model."""
        return build_adk_model(self._resolve_provider(provider), self._settings)

    def evidence_tools(
        self, provider: str | None = None, scope: RegulationScope | None = None
    ) -> EvidenceTools:
        """The agents' on-demand deterministic tools (one framework-agnostic
        core), bound to the same regulation scope as the evidence stage."""
        scope = scope or RegulationScope(self.regulation())
        chaos = self.chaos(strict=scope.strict)
        return EvidenceTools(
            calc_engine=self.calc_engine(),
            meta_provider=chaos,
            strategy_provider=self.strategy(provider, strict=scope.strict),
            default_gen=self._settings.calc_gen,
            scope=scope,
            regulation_catalog=chaos,
        )

    def ground_truth(
        self, provider: str | None = None, scope: RegulationScope | None = None
    ) -> GroundTruthAssembler:
        """The deterministic evidence stage shared by every orchestrator,
        bound to a regulation scope (pinned by the controller, or auto)."""
        scope = scope or RegulationScope(self.regulation())
        chaos = self.chaos(strict=scope.strict)
        calc = self.calc_engine()
        return GroundTruthAssembler(
            meta_provider=chaos,
            calc_engine=calc,
            strategy_provider=self.strategy(provider, strict=scope.strict),
            default_gen=self._settings.calc_gen,
            suggestion_source=self.smogon_dex(),
            scope=scope,
            regulation_catalog=chaos,
            guard=RegulationGuard(calc, self._settings.calc_gen),
        )

    def build_native_pipeline(
        self, provider: str | None = None, regulation: str | None = None
    ) -> AnalysisPipeline:
        """Assemble the hand-rolled :class:`AnalysisService`."""
        name = self._resolve_provider(provider)
        llm = self.build_llm(name)
        prompts = self.prompts()
        return AnalysisService(
            parser=ShowdownReplayParser(),
            selector=LLMSelectionService(llm, prompts=prompts, temperature=0.0),
            evidence=self.ground_truth(name, RegulationScope(self.regulation(regulation))),
            llm=llm,
            memory=self.memory(),
            prompts=prompts,
        )

    def build_langchain_pipeline(
        self, provider: str | None = None, regulation: str | None = None
    ) -> AnalysisPipeline:
        """Assemble the LangChain LCEL orchestrator."""
        name = self._resolve_provider(provider)
        scope = RegulationScope(self.regulation(regulation))
        return LangChainAnalysisOrchestrator(
            parser=ShowdownReplayParser(),
            chat_model=self.build_chat_model(name),
            evidence=self.ground_truth(name, scope),
            memory=self.memory(),
            prompts=self.prompts(),
            tools=build_langchain_tools(self.evidence_tools(name, scope)),
            provider_name=f"langchain:{name}",
        )

    def build_adk_pipeline(
        self, provider: str | None = None, regulation: str | None = None
    ) -> AnalysisPipeline:
        """Assemble the Google ADK orchestrator (default backend)."""
        name = self._resolve_provider(provider)
        scope = RegulationScope(self.regulation(regulation))
        return AdkAnalysisOrchestrator(
            parser=ShowdownReplayParser(),
            model=self.build_adk_model(name),
            evidence=self.ground_truth(name, scope),
            memory=self.memory(),
            prompts=self.prompts(),
            tools=build_adk_tools(self.evidence_tools(name, scope)),
            provider_name=f"adk:{name}",
            agent_timeout_seconds=self._settings.agent_timeout_seconds,
        )

    def build_pipeline(
        self,
        provider: str | None = None,
        orchestrator: str | None = None,
        regulation: str | None = None,
    ) -> AnalysisPipeline:
        """Assemble the configured pipeline (orchestrator-agnostic entrypoint).

        ``regulation`` overrides the configured controller for this pipeline:
        "auto", a code such as "mb"/"mc", or a full format id.
        """
        backend = (orchestrator or self._settings.orchestrator).lower()
        if backend not in _ORCHESTRATORS:
            raise ConfigurationError(
                f"Unknown orchestrator '{backend}'. Available: {sorted(_ORCHESTRATORS)}"
            )
        if backend == "adk":
            return self.build_adk_pipeline(provider, regulation)
        if backend == "langchain":
            return self.build_langchain_pipeline(provider, regulation)
        return self.build_native_pipeline(provider, regulation)

    def usage_quota(self) -> UsageQuotaService:
        """The per-visitor daily quota (ADR-036); a no-op without a limit.

        Raises:
            ConfigurationError: A limit is set without a usage_quota_secret,
                or the Firestore client cannot be built.
        """
        if self._usage_quota is None:
            limits = {"openai": self._settings.openai_daily_analysis_limit}
            store: FirestoreUsageQuotaStore | None = None
            secret = self._settings.usage_quota_secret or ""
            if any(limits.values()):
                if not secret:
                    raise ConfigurationError(
                        "PROFESSORVGC_USAGE_QUOTA_SECRET is required when a daily "
                        "analysis limit is set (it keys the visitor HMAC)."
                    )
                client = build_firestore_client(
                    self._settings.firestore_project_id or "",
                    self._settings.firestore_database_id,
                    self._settings.firestore_credentials_path,
                    self._settings.firestore_grpc_ca_bundle_path,
                )
                store = FirestoreUsageQuotaStore(
                    client, collection=self._settings.usage_quota_collection
                )
            self._usage_quota = UsageQuotaService(store, limits, secret=secret)
        return self._usage_quota

    def data_warnings(self) -> list[str]:
        """Chaos tiers refused because their data names another format (only
        once the repository has been built by an analysis)."""
        if self._chaos_repo is None:
            return []
        return [
            f"Chaos tier '{tier}' was ignored: it {reason}. Re-sync it from Smogon "
            "(scripts/sync_smogon_chaos_to_firestore.py)."
            for tier, reason in self._chaos_repo.rejected_tiers.items()
        ]

    @staticmethod
    def resolve_replay_text(text: str) -> str:
        """The pasted text, or the replay JSON when it is a Showdown replay URL.

        Raises:
            ReplayFetchError: The URL was recognized but could not be fetched.
        """
        url = normalize_replay_json_url(text) if text else None
        return fetch_replay_json(url) if url is not None else text

    @staticmethod
    def parse_replay_for_viewer(text: str) -> BattleReplay:
        """Turn-by-turn view for the UI's battle panel (same log parser, ADR-037)."""
        return parse_replay_for_viewer(text)

    def shutdown(self) -> None:
        """Release long-lived resources (Node subprocesses, Firestore gRPC channel)."""
        if self._calc is not None:
            self._calc.close()
            self._calc = None
        if self._dex is not None:
            self._dex.close()
            self._dex = None
        if self._chaos_repo is not None:
            self._chaos_repo.close()
            self._chaos_repo = None
