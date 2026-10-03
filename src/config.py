"""Application settings, read from the environment (``.env`` supported) and
injected into the composition root (``src/services/container.py``)."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.domain.exceptions import ConfigurationError
from src.domain.regulation import parse_regulation_setting

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Project rule: Gemini 3.5+ only. Checked when Settings is built and again
# when a Gemini client is built (llm/base.py), for long-running processes.
_GEMINI_MODEL_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?")
MIN_GEMINI_VERSION = (3, 5)


def parse_gemini_version(model: str) -> tuple[int, int] | None:
    """The ``(major, minor)`` of a ``gemini-X[.Y]`` model id, else ``None``."""
    match = _GEMINI_MODEL_RE.match((model or "").strip().lower())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2) or 0))


class Settings(BaseSettings):
    """Typed, validated application settings sourced from env / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PROFESSORVGC_",
        extra="ignore",
    )

    # --- Paths ---------------------------------------------------------- #
    project_root: Path = _PROJECT_ROOT
    reg_fallback_depth: int = 3
    node_calc_dir: Path = Field(default=_PROJECT_ROOT / "node_calc")

    # --- Chaos data: Firestore is the only source (DATA.md) -------------- #
    firestore_project_id: str | None = None
    firestore_database_id: str = "(default)"
    firestore_chaos_collection: str = "chaos_tiers"
    # Service account key; unset = Application Default Credentials.
    firestore_credentials_path: str | None = None
    # Extra CA roots for gRPC on TLS-intercepting networks (DATA.md).
    firestore_grpc_ca_bundle_path: str | None = None

    # --- Per-visitor daily quota for paid providers (ADR-036) ------------ #
    # 0 = unlimited. A limit requires the secret that keys the visitor HMAC.
    openai_daily_analysis_limit: int = Field(default=0, ge=0)
    usage_quota_collection: str = "usage_quota"
    usage_quota_secret: str | None = None

    # --- Node / calc engine -------------------------------------------- #
    node_binary: str = "node"
    calc_gen: int = 9
    calc_timeout_seconds: float = 20.0

    # --- Official Smogon data via @pkmn/smogon (needs network) ----------- #
    use_smogon_dex: bool = False
    smogon_dex_timeout_seconds: float = 30.0

    # --- Semantic retrieval over Smogon prose (needs use_smogon_dex; ADR-027)
    use_semantic_strategy: bool = False
    openai_embedding_model: str = "text-embedding-3-small"
    gemini_embedding_model: str = "models/text-embedding-004"
    semantic_strategy_top_k: int = 3

    # --- Orchestration backend ("adk" | "langchain" | "native") ------- #
    orchestrator: str = "adk"

    # --- Regulation controller (ADR-035) ------------------------------- #
    # "auto" follows the replay; a code ("mb", "mc") or format id pins it.
    regulation: str = "auto"
    regulation_format_prefix: str = "gen9championsvgc2026reg"

    # --- LLM (bring your own key) -------------------------------------- #
    default_provider: str = "gemini"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    llm_temperature: float = 0.2
    # Per ADK agent turn; a Cloud Run session's first call also pays cold
    # starts (Node workers, gRPC channel, model client).
    agent_timeout_seconds: float = 240.0

    @field_validator("regulation")
    @classmethod
    def _regulation_is_known_shape(cls, value: str, info: ValidationInfo) -> str:
        prefix = str(info.data.get("regulation_format_prefix") or "gen9championsvgc2026reg")
        try:
            parse_regulation_setting(value, prefix)
        except ConfigurationError as exc:
            raise ValueError(str(exc)) from exc
        return value.strip().lower()

    @field_validator("gemini_model")
    @classmethod
    def _require_modern_gemini_model(cls, value: str) -> str:
        """Refuse a Gemini model below ``MIN_GEMINI_VERSION`` at startup."""
        version = parse_gemini_version(value)
        if version is None or version < MIN_GEMINI_VERSION:
            min_str = ".".join(str(part) for part in MIN_GEMINI_VERSION)
            raise ValueError(
                f"PROFESSORVGC_GEMINI_MODEL='{value}' is not supported — this "
                f"project requires Gemini {min_str} or newer (e.g. "
                f"'gemini-3.5-flash')."
            )
        return value

    # --- Chaos extraction tunables ------------------------------------- #
    chaos_top_n: int = 3

    @property
    def calc_server_script(self) -> Path:
        """Absolute path to the Node IPC entrypoint."""
        return self.node_calc_dir / "calc_server.js"

    @property
    def smogon_dex_script(self) -> Path:
        """Absolute path to the @pkmn/smogon dex IPC worker."""
        return self.node_calc_dir / "smogon_dex_server.js"


def load_settings() -> Settings:
    """Build a :class:`Settings` instance (factory for DI / testability)."""
    return Settings()
