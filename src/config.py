"""Application configuration.

All secrets and tunables are read from the environment (``.env`` supported).
Nothing here imports business logic — configuration is infrastructure and is
injected into the composition root (``src/services/container.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# This project's own requirement (not a Google-imposed floor): only Gemini
# 3.5 and newer may ever be configured — a competition rule, enforced here
# as an actual guarantee rather than just a default value someone could
# still override with an older id. Checked at Settings CONSTRUCTION time
# (below) — the earliest possible point, before the app even finishes
# starting up — and AGAIN at every point a Gemini client actually gets
# built (src/adapters/llm/base.py's require_modern_gemini_model, which
# reuses this same parser) as defense in depth for an already-running
# process whose cached Settings predate a later config change.
_GEMINI_MODEL_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?")
MIN_GEMINI_VERSION = (3, 5)


def parse_gemini_version(model: str) -> tuple[int, int] | None:
    """Parse the leading "gemini-X[.Y]" version out of a model id string.
    Returns ``None`` when the string doesn't match that shape at all."""
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

    # --- Chaos data: Google Cloud Firestore, unconditionally ------------ #
    # The running app has exactly ONE Chaos data source — Firestore — with
    # no local-file fallback and no config knob to select one: see
    # Container.chaos_repository(), which always builds a
    # FirestoreChaosRepository. This is a deliberate, competition-driven
    # requirement (the app must genuinely depend on Firestore, not merely
    # default to it), not just a preference. Populate it with
    # scripts/migrate_chaos_to_firestore.py (from a local Chaos dump) and/or
    # scripts/sync_smogon_chaos_to_firestore.py (live from Smogon) — both
    # remain local-file-aware as OFFLINE DATA-LOADING TOOLS, which is a
    # different concern entirely from what the running app itself reads
    # to answer a question. See DATA.md.
    firestore_project_id: str | None = None
    firestore_database_id: str = "(default)"
    firestore_chaos_collection: str = "chaos_tiers"
    # Path to a service account JSON key. Unset = Application Default
    # Credentials (gcloud auth application-default login, or
    # GOOGLE_APPLICATION_CREDENTIALS already set in the environment).
    firestore_credentials_path: str | None = None
    # Optional: a CA bundle (PEM) gRPC should trust, IN ADDITION TO its own
    # built-in roots — needed only on a machine where something TLS-
    # intercepts outbound HTTPS with a locally-installed root cert (a
    # security suite's "web shield"/"SSL scanning" feature, a corporate
    # proxy, ...), which grpc's own bundled roots.pem has no way to know
    # about (unlike Python's `ssl` module, which `pip-system-certs` already
    # patches to read the OS trust store — grpc uses its own TLS stack).
    # See DATA.md's Firestore section for how to detect this and generate
    # the bundle. Unset = grpc's normal default roots only.
    firestore_grpc_ca_bundle_path: str | None = None

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

    # --- Orchestration backend ("adk" | "langchain" | "native") ------- #
    orchestrator: str = "adk"

    # --- LLM (bring your own key) -------------------------------------- #
    # Defaults to Gemini as the showcased provider (paired with
    # orchestrator="adk" above and the Firestore-only Chaos backend — a
    # full Google-stack demo path); "openai" remains fully supported, just
    # no longer the default.
    default_provider: str = "gemini"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    llm_temperature: float = 0.2

    @field_validator("gemini_model")
    @classmethod
    def _require_modern_gemini_model(cls, value: str) -> str:
        """Fail at Settings CONSTRUCTION time — app startup — for any
        Gemini model below MIN_GEMINI_VERSION, regardless of which
        provider happens to be selected right now. See this module's own
        top-of-file comment for why this is checked here AND again at
        point-of-use."""
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
