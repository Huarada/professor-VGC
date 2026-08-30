"""Tests for src/config.py's own validation — the earliest possible point
a bad Gemini model id can be caught (before any adapter is ever built).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings, parse_gemini_version


def test_parse_gemini_version_reads_major_and_minor():
    assert parse_gemini_version("gemini-3.5-flash") == (3, 5)
    assert parse_gemini_version("gemini-3.7-flash") == (3, 7)


def test_parse_gemini_version_defaults_a_missing_minor_to_zero():
    assert parse_gemini_version("gemini-4-flash") == (4, 0)


def test_parse_gemini_version_returns_none_for_unparseable_input():
    assert parse_gemini_version("gpt-4o-mini") is None
    assert parse_gemini_version("") is None


def test_settings_construction_rejects_a_stale_gemini_model_immediately():
    """Fails at Settings() construction — app startup — not lazily on
    first actual Gemini call. Regardless of which provider is currently
    selected: this project's own competition requirement is that Gemini
    3.5+ is guaranteed, not merely defaulted to."""
    with pytest.raises(ValidationError, match="3.5"):
        Settings(_env_file=None, gemini_model="gemini-1.5-flash")


def test_settings_construction_accepts_the_configured_default():
    settings = Settings(_env_file=None)
    assert settings.gemini_model == "gemini-3.5-flash"
    assert settings.default_provider == "gemini"


def test_agent_timeout_seconds_defaults_and_is_overridable():
    """PROFESSORVGC_AGENT_TIMEOUT_SECONDS -- made configurable (was a
    hardcoded 180 in AdkAnalysisOrchestrator) after a live finding: a
    session's first request on Cloud Run pays one-time cold costs (its
    own Node subprocesses, a fresh Firestore channel, a fresh ADK/genai
    client) a long-running local dev process never pays, needing more
    headroom than 180s comfortably covers locally."""
    assert Settings(_env_file=None).agent_timeout_seconds == 240.0
    assert Settings(_env_file=None, agent_timeout_seconds=90.0).agent_timeout_seconds == 90.0
