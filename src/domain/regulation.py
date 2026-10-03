"""Regulations: which format's data an analysis may use.

Usage data, Smogon sets and the Pokemon the explanation names must come from
the game's own regulation. Pure domain logic: format-id parsing, Bo3 -> Bo1,
and the per-analysis :class:`RegulationScope`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from src.domain.exceptions import ConfigurationError, RegulationMismatchError

_FORMAT_RE = re.compile(
    r"^(?P<base>gen\d+[a-z]*?vgc\d{4}reg(?P<code>[a-z]+?))(?P<bo3>bo3)?$"
)
_SHORT_CODE_RE = re.compile(r"^[a-z]{1,3}$")

AUTO = "auto"


def normalize_species(name: str) -> str:
    """The normalization usage data is keyed by: lowercase, with spaces,
    hyphens, dots and apostrophes dropped (``"Mr. Rime"`` -> ``"mrrime"``)."""
    return re.sub(r"[\s\-.'’]", "", name.lower())


class RegulationRoster(BaseModel):
    """Which Pokemon a regulation allows, from usage data.

    ``legal``: species with data in this regulation's own tiers. ``known``:
    species any regulation of the same game lists, so a forme listed elsewhere
    (a Mega) must be listed here too.
    """

    model_config = ConfigDict(frozen=True)

    legal: frozenset[str] = frozenset()
    known: frozenset[str] = frozenset()

    @property
    def empty(self) -> bool:
        """No data for this regulation: legality cannot be verified."""
        return not self.legal

    def allows(self, name: str) -> bool:
        """Whether ``name`` is legal here; unlisted battle formes (``Palafin-Hero``)
        resolve to their base species.
        """
        normalized = normalize_species(name)
        if normalized in self.legal:
            return True
        if normalized in self.known:
            return False
        parts = name.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            prefix = normalize_species("-".join(parts[:cut]))
            if prefix in self.legal:
                return True
            if prefix in self.known:
                return False
        return False


class Regulation(BaseModel):
    """One regulation, identified by its Bo1 Showdown format id."""

    model_config = ConfigDict(frozen=True)

    format_id: str
    """Canonical (Bo1) format id, e.g. ``gen9championsvgc2026regmb`` — the id
    whose usage data and Smogon sets describe this regulation."""
    code: str
    """Regulation letters, e.g. ``"mb"``."""

    @classmethod
    def from_format(cls, format_id: str | None) -> "Regulation | None":
        """The regulation a Showdown format id belongs to (Bo3 included), or
        None for a format that is not a regulation-based VGC format."""
        match = _FORMAT_RE.match((format_id or "").strip().lower())
        if match is None:
            return None
        return cls(format_id=match.group("base"), code=match.group("code"))

    @property
    def label(self) -> str:
        """Human label: ``mb`` -> ``Reg M-B``, ``h`` -> ``Reg H``."""
        code = self.code.upper()
        return f"Reg {code[0]}-{code[1:]}" if len(code) > 1 else f"Reg {code}"

    @property
    def family(self) -> str:
        """The game/year prefix regulations of the same game share, e.g.
        ``gen9championsvgc2026`` for Reg M-A/M-B/M-C."""
        return self.format_id[: -len("reg" + self.code)]

    def includes(self, format_id: str | None) -> bool:
        """Whether ``format_id`` (Bo1 or Bo3) belongs to this regulation."""
        other = Regulation.from_format(format_id)
        return other is not None and other.format_id == self.format_id


def parse_regulation_setting(value: str, format_prefix: str) -> Regulation | None:
    """``"auto"`` -> None (follow the replay); ``"mb"`` -> ``<prefix>mb``;
    a full format id is accepted as-is.

    Raises:
        ConfigurationError: The value is neither ``auto``, a short regulation
            code nor a regulation-based VGC format id.
    """
    text = (value or AUTO).strip().lower()
    if text == AUTO:
        return None
    candidate = f"{format_prefix}{text}" if _SHORT_CODE_RE.match(text) else text
    regulation = Regulation.from_format(candidate)
    if regulation is None:
        raise ConfigurationError(
            f"Unknown regulation {value!r}: use 'auto', a regulation code such as "
            f"'mb' / 'mc' (prefix {format_prefix!r}), or a full VGC format id."
        )
    return regulation


class RegulationScope:
    """The regulation one analysis is bound to, shared by the evidence stage and
    the agent tools. ``pinned`` = strict (no other regulation's data; another
    regulation's replay is refused); ``None`` = follow the replay.
    """

    def __init__(self, pinned: Regulation | None = None) -> None:
        self.pinned = pinned
        self._current: Regulation | None = pinned

    @property
    def strict(self) -> bool:
        return self.pinned is not None

    @property
    def current(self) -> Regulation | None:
        return self._current

    @property
    def format_id(self) -> str | None:
        """The data format to query, or None when no regulation is known."""
        return self._current.format_id if self._current else None

    def bind(self, replay_format: str | None, *, has_replay: bool = True) -> Regulation | None:
        """Resolve the regulation for one analysis.

        Raises:
            RegulationMismatchError: The scope is pinned and the replay belongs
                to a different regulation.
        """
        replay_regulation = Regulation.from_format(replay_format) if has_replay else None
        if self.pinned is not None:
            if replay_regulation is not None and replay_regulation != self.pinned:
                raise RegulationMismatchError(
                    f"This replay is {replay_regulation.label} ({replay_format}) but the "
                    f"analysis is pinned to {self.pinned.label}. Switch the regulation "
                    f"controller to {replay_regulation.label} (or 'auto') to analyze it — "
                    "data from one regulation is never used for another."
                )
            self._current = self.pinned
        else:
            self._current = replay_regulation
        return self._current
