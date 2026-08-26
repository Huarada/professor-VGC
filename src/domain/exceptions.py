"""Domain-specific exception hierarchy.

Every layer raises typed exceptions deriving from :class:`ProfessorVGCError`,
so the presentation layer can catch one base type while still being able to
discriminate failures (parsing vs. calc engine vs. LLM) when needed.
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
    """The deterministic damage-calc engine failed.

    Wraps transport-level (IPC/subprocess) failures as well as invalid
    payloads returned by the Node ``@smogon/calc`` subsystem.
    """


class StrategyKnowledgeError(ProfessorVGCError):
    """The Smogon strategy/archetype knowledge source failed."""


class LLMProviderError(ProfessorVGCError):
    """An LLM provider call failed at transport level."""


class LLMResponseValidationError(ProfessorVGCError):
    """An LLM response did not satisfy its expected contract."""


class ConversationMemoryError(ProfessorVGCError):
    """The conversation memory backend failed."""


class ReplayFetchError(ProfessorVGCError):
    """Fetching a replay from a pasted Showdown replay URL failed.

    Deliberately distinct from :class:`LogParsingError`: this is a
    RETRIEVAL failure (network, timeout, HTTP 404/5xx, empty body) — the
    replay content was never obtained at all, as opposed to content that
    was obtained but couldn't be parsed.
    """
