"""Prompt template loading (no hardcoded prompts anywhere else)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template by file name (without extension)."""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
