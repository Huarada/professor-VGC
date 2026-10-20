"""Deterministic regulation guard: no Pokemon from another regulation.

The evidence is already scoped to one regulation (only its own usage data and
Smogon sets), but an LLM can still bring a Pokemon in from its own memory —
e.g. recommend a Pokemon that only exists in Reg M-C while analyzing a Reg
M-B game. This guard recognizes every species name in the text (from the
engine's own dex) and flags the ones that are not legal in the regulation
(no usage data in that regulation's own tiers), so the answer can be
corrected before it reaches the user — and the user is warned if it isn't.
"""

from __future__ import annotations

import re
from typing import Iterable

from src.domain.interfaces import SpeciesCatalog
from src.domain.regulation import RegulationRoster, normalize_species


_MEGA_FORM = re.compile(r"^(?P<base>.+)-Mega(?:-(?P<letter>[XYZ]))?$")


def _mega_aliases(dex: set[str]) -> dict[str, str]:
    """How prose writes a Mega: ``Garchomp-Mega-Z`` -> ``Mega Garchomp Z``,
    ``Salamence-Mega`` -> ``Mega Salamence``."""
    aliases: dict[str, str] = {}
    for name in dex:
        match = _MEGA_FORM.match(name)
        if match:
            letter = f" {match['letter']}" if match["letter"] else ""
            aliases[f"Mega {match['base']}{letter}"] = name
    return aliases


class RegulationGuard:
    """Finds species named in free text that a regulation does not allow."""

    def __init__(self, catalog: SpeciesCatalog | None, gen: int = 9) -> None:
        self._catalog = catalog
        self._gen = gen
        self._pattern: re.Pattern[str] | None = None
        self._names: list[str] = []
        self._aliases: dict[str, str] = {}  # prose spelling -> dex name

    def _species_pattern(self) -> re.Pattern[str] | None:
        if self._pattern is None and self._catalog is not None:
            dex = set(self._catalog.species_names(self._gen))
            self._names = sorted(dex, key=len, reverse=True)
            self._aliases = _mega_aliases(dex)
            if self._names:
                # Case-sensitive on purpose: prose capitalizes Pokemon names,
                # which keeps ordinary words ("ditto") from matching.
                spellings = sorted({*self._names, *self._aliases}, key=len, reverse=True)
                alternation = "|".join(re.escape(n) for n in spellings)
                self._pattern = re.compile(rf"(?<![\w-])({alternation})(?![\w-])")
        return self._pattern

    def legal_display_names(self, legal: frozenset[str]) -> list[str]:
        """Display names (from the dex) of every legal species, sorted."""
        if self._species_pattern() is None:
            return sorted(legal)
        return sorted(n for n in self._names if normalize_species(n) in legal)

    def mentioned(self, text: str) -> list[str]:
        """Species named in ``text``, in order of first appearance."""
        pattern = self._species_pattern()
        if pattern is None:
            return []
        found = (self._aliases.get(m.group(1), m.group(1)) for m in pattern.finditer(text))
        return list(dict.fromkeys(found))

    def violations(
        self, text: str, roster: RegulationRoster, also_allowed: Iterable[str] = ()
    ) -> list[str]:
        """Species ``text`` names that the regulation does not allow
        (``also_allowed``: species actually in the analyzed game, legal by
        construction)."""
        if roster.empty:
            return []
        allowed = {normalize_species(n) for n in also_allowed}
        return [
            name for name in self.mentioned(text)
            if not roster.allows(name) and normalize_species(name) not in allowed
        ]

    @staticmethod
    def correction_note(violations: list[str], label: str) -> str:
        names = ", ".join(violations)
        return (
            f"REGULATION CHECK FAILED — your previous answer mentioned {names}, "
            f"which {'is' if len(violations) == 1 else 'are'} not legal in {label} "
            "(no usage data in this regulation). Rewrite the complete answer without "
            f"presenting {'it' if len(violations) == 1 else 'them'} as part of {label}: "
            "only Pokemon listed in regulation.legal_species (or in the game itself) "
            "may be recommended, compared or described as part of this regulation."
        )
