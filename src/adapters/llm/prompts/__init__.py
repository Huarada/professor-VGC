"""Prompt template loading (no hardcoded prompts anywhere else).

Services never import this module: they depend on the
:class:`~src.domain.interfaces.PromptRepository` port, and the composition
root injects :class:`FilePromptRepository`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from src.domain.exceptions import ConfigurationError

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template by file name (without extension)."""
    path = _PROMPT_DIR / f"{name}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Unknown prompt '{name}' (expected {path}).") from exc


class FilePromptRepository:
    """:class:`~src.domain.interfaces.PromptRepository` over the bundled ``*.txt`` files."""

    def get(self, name: str) -> str:
        return load_prompt(name)
