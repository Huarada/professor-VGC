"""Tolerance bands: when is a damage figure "consistent" with another?

An exact match is the wrong bar for damage. Two sources of legitimate
variance are modelled explicitly instead:

- ``absolute_pp``: the log shows HP as whole percents, so an observed damage
  value (the difference of two readings) carries about ±2 percentage points
  of rounding;
- ``relative``: a range computed under one assumed spread (EVs/nature/item)
  moves proportionally when the real Pokemon's investment differs — the
  default 5% widens each bound by 5% of itself.

A band widens the reference range ``[lo, hi]`` to
``[lo * (1 - relative) - absolute_pp, hi * (1 + relative) + absolute_pp]``.
Every report prints the band it used, and ``SENSITIVITY_BANDS`` re-scores the
same data at several relative tolerances so a conclusion never hinges on
one arbitrary choice.
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts.faithfulness_benchmark.observed_damage import HP_ROUNDING_PP


@dataclass(frozen=True)
class ToleranceBand:
    absolute_pp: float = 2 * HP_ROUNDING_PP
    relative: float = 0.05

    def widen(self, lo: float, hi: float) -> tuple[float, float]:
        low, high = sorted((lo, hi))
        return (
            low * (1 - self.relative) - self.absolute_pp,
            high * (1 + self.relative) + self.absolute_pp,
        )

    def contains(self, lo: float, hi: float, value: float) -> bool:
        low, high = self.widen(lo, hi)
        return low <= value <= high

    def reaches(self, hi: float, value: float) -> bool:
        """Whether the widened upper bound reaches ``value`` (a KO threshold)."""
        return self.widen(hi, hi)[1] >= value

    @property
    def label(self) -> str:
        return f"±{self.absolute_pp:g}pp ±{self.relative:.0%}"


DEFAULT_BAND = ToleranceBand()
EXACT_BAND = ToleranceBand(relative=0.0)
SENSITIVITY_BANDS = tuple(ToleranceBand(relative=r) for r in (0.0, 0.05, 0.10, 0.15))
