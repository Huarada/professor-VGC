"""Tests for shared LLM-adapter helpers (src/adapters/llm/base.py)."""

from __future__ import annotations

import pytest

from src.adapters.llm.base import require_modern_gemini_model
from src.adapters.llm.gemini_provider import GeminiProvider
from src.domain.exceptions import ConfigurationError


def test_accepts_the_minimum_supported_version():
    require_modern_gemini_model("gemini-3.5-flash")  # must not raise


def test_accepts_newer_versions():
    require_modern_gemini_model("gemini-3.6-flash")
    require_modern_gemini_model("gemini-3.7-flash")
    require_modern_gemini_model("gemini-4.0-flash")


def test_accepts_a_bare_major_version_at_or_above_the_floor():
    """A model id with no minor version (e.g. a future "gemini-4-flash")
    must not be misread as version 4.0 < 4.5 or similar off-by-one bug —
    missing minor defaults to 0, and 4.0 >= 3.5 is correctly accepted."""
    require_modern_gemini_model("gemini-4-flash")


def test_rejects_versions_below_the_floor():
    for stale in ("gemini-1.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-3.4-flash"):
        with pytest.raises(ConfigurationError, match="3.5"):
            require_modern_gemini_model(stale)


def test_rejects_unparseable_or_empty_model_ids():
    for bad in ("", "not-a-gemini-model", "gpt-4o-mini"):
        with pytest.raises(ConfigurationError):
            require_modern_gemini_model(bad)


def test_error_message_explains_the_stale_streamlit_session_cause():
    """The message this project actually hit live: an already-running
    Streamlit session caches its Container/Settings at first use, so
    updating PROFESSORVGC_GEMINI_MODEL alone doesn't fix an in-progress
    session — the message must say so, not just name the bad model id."""
    with pytest.raises(ConfigurationError, match="Reset conversation|restart"):
        require_modern_gemini_model("gemini-1.5-flash")


def test_gemini_provider_construction_rejects_a_stale_model_id():
    with pytest.raises(ConfigurationError, match="3.5"):
        GeminiProvider(api_key="fake-key", model="gemini-1.5-flash")
