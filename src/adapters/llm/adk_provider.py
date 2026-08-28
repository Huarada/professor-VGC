"""Google ADK (Agent Development Kit) integration for the LLM boundary.

Mirrors ``src/adapters/llm/langchain_provider.py``'s role for this backend:
a single BYOK factory, :func:`build_adk_model`, that returns whatever object
``google.adk.agents.Agent(model=...)`` expects. ADK itself must never leak
into domain or services signatures — only ``src/services/adk_orchestrator.py``
(the composition root's ADK backend) imports from here.

Two independent model paths, one per supported provider (mirrors the exact
BYOK shape ``langchain_provider.build_chat_model`` already uses for
openai/gemini):

- **gemini** — ADK's native path: a plain model-id string. ``google-genai``
  (the SDK ADK's own Gemini support is built on) is already a core
  ``google-adk`` dependency, so no extra package is needed. ADK reads the
  API key from the ``GOOGLE_API_KEY`` environment variable for this path —
  there is no per-call/per-Agent key parameter in the public API — so BYOK
  is honored the same way :class:`~src.adapters.llm.gemini_provider.
  GeminiProvider` already does it (``genai.configure(api_key=...)`` is
  likewise a process-global call): this module sets that env var right
  before constructing the model, once per :func:`build_adk_model` call.
- **openai** — ADK has no native OpenAI model class; its own documented
  answer for every non-Gemini vendor is the ``LiteLlm`` wrapper (a thin
  adapter over the separately-installed ``litellm`` package). Same
  env-var-BYOK shape as the Gemini path, using ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from src.adapters.llm.base import require_modern_gemini_model
from src.config import Settings
from src.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    # Type-checking only — every runtime use stays a local, lazy import
    # (below) so this module remains importable without google-adk/litellm
    # installed, matching langchain_provider.py's own degrade-to-
    # ConfigurationError pattern instead of an ImportError at module load.
    from google.adk.models import BaseLlm

_SUPPORTED = ("openai", "gemini")


def build_adk_model(provider: str, settings: Settings) -> "str | BaseLlm":
    """BYOK factory returning the ``model=`` argument for an ADK ``Agent``."""
    name = (provider or settings.default_provider).lower()
    if name not in _SUPPORTED:
        raise ConfigurationError(
            f"Unknown provider '{name}'. Available: {list(_SUPPORTED)}"
        )
    if name == "gemini":
        if not settings.gemini_api_key:
            raise ConfigurationError(
                "Gemini API key required (PROFESSORVGC_GEMINI_API_KEY)"
            )
        require_modern_gemini_model(settings.gemini_model)
        os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key
        return settings.gemini_model
    if not settings.openai_api_key:
        raise ConfigurationError("OpenAI API key required (PROFESSORVGC_OPENAI_API_KEY)")
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ConfigurationError(
            "The 'litellm' package is not installed (ADK's own path to OpenAI "
            "models). Run: pip install litellm"
        ) from exc
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    # LiteLLM's own routing convention: "<provider>/<model-id>".
    return LiteLlm(model=f"openai/{settings.openai_model}")
