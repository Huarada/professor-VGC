"""Domain exceptions: every layer raises a :class:`ProfessorVGCError` subclass,
so the UI can catch one base type and still tell failures apart.
"""

from __future__ import annotations


class ProfessorVGCError(Exception):
    """Base class for every error raised inside the ProfessorVGC domain."""


class ConfigurationError(ProfessorVGCError):
    """Required configuration (keys, paths, binaries) is missing/invalid."""


class LogParsingError(ProfessorVGCError):
    """A Showdown replay/log cannot be parsed into a :class:`GameState`."""


class ChaosDataError(ProfessorVGCError):
    """The Chaos statistics file is missing or malformed."""


class CalcEngineError(ProfessorVGCError):
    """The damage-calc engine failed (IPC transport or invalid payload)."""


class StrategyKnowledgeError(ProfessorVGCError):
    """The Smogon strategy/archetype knowledge source failed."""


class LLMProviderError(ProfessorVGCError):
    """An LLM provider call failed at transport level."""


class LLMResponseValidationError(ProfessorVGCError):
    """An LLM response did not satisfy its expected contract."""


class ConversationMemoryError(ProfessorVGCError):
    """The conversation memory backend failed."""


class ReplayFetchError(ProfessorVGCError):
    """A pasted replay URL could not be fetched (network, HTTP error, empty body)
    — retrieval, unlike :class:`LogParsingError`.
    """


class RegulationMismatchError(ProfessorVGCError):
    """The replay belongs to a different regulation than the pinned one; refused
    rather than mixing regulations' data.
    """


class UsageQuotaError(ProfessorVGCError):
    """The usage quota could not be checked; the analysis is refused (fail closed)."""


class UsageLimitExceededError(UsageQuotaError):
    """The visitor already used every analysis the daily quota allows for
    this provider."""
