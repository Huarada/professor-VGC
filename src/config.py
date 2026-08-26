"""Application configuration.

All secrets and tunables are read from the environment (``.env`` supported).
Nothing here imports business logic — configuration is infrastructure and is
injected into the composition root (``src/services/container.py``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    # Directory of Chaos files (per rating tier / regulation) OR a single file.
    chaos_data_path: Path = Field(default=_PROJECT_ROOT / "data" / "chaos")
    reg_fallback_depth: int = 3
    node_calc_dir: Path = Field(default=_PROJECT_ROOT / "node_calc")

    # --- Node / calc engine -------------------------------------------- #
    node_binary: str = "node"
    calc_gen: int = 9
    calc_timeout_seconds: float = 20.0

    # --- Official Smogon data via @pkmn/smogon (needs network at runtime) --- #
    use_smogon_dex: bool = False
    smogon_dex_timeout_seconds: float = 30.0

    # --- Semantic retrieval over official Smogon analysis prose ---------- #
    # Ranks passages of Smogon's own analysis text (per format, per set)
    # against the user's actual question instead of always using the first
    # available format's overview verbatim. Requires use_smogon_dex=true
    # (nothing to retrieve over otherwise) and network access for embeddings
    # (uses whichever provider/key is already configured — no separate
    # credential). See ADR-027.
    use_semantic_strategy: bool = False
    openai_embedding_model: str = "text-embedding-3-small"
    gemini_embedding_model: str = "models/text-embedding-004"
    semantic_strategy_top_k: int = 3

    # --- Orchestration backend ("langchain" | "native") --------------- #
    orchestrator: str = "langchain"

    # --- LLM (bring your own key) -------------------------------------- #
    default_provider: str = "openai"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"
    llm_temperature: float = 0.2

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
