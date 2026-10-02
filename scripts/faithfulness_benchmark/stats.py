"""Formal statistical significance for the damage_range comparison.

Fisher's exact test on the 2x2 contingency table of correct/incorrect
counts — the right tool for this benchmark's small-n regime (a chi-square
test's normal approximation is unreliable once any cell's expected count
drops below ~5, which every run of this benchmark hits). Fisher's exact
test computes the exact probability of the observed table (or a more
extreme one) under the hypergeometric null of "no association between
condition and correctness", with no sample-size assumption at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import fisher_exact
from scipy.stats.contingency import odds_ratio as _odds_ratio

CONFIDENCE = 0.95


def wilson_interval(successes: int, total: int, confidence: float = CONFIDENCE) -> tuple[float, float]:
    """Wilson score interval for a proportion — the rate's plausible range.

    Reported instead of a bare point estimate: with n in the low hundreds a
    rate is only known to within several percentage points, and Wilson stays
    well-behaved near 0% / 100% and at small n (unlike the normal
    approximation). Returns ``(nan, nan)`` when ``total`` is 0.
    """
    if total <= 0:
        return (math.nan, math.nan)
    from scipy.stats import norm

    z = float(norm.ppf(0.5 + confidence / 2))
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == total else min(1.0, centre + half)
    return (low, high)


def format_rate(successes: int, total: int, confidence: float = CONFIDENCE) -> str:
    """``"71.0% (95% CI 63.4-77.5%, 110/155)"``."""
    if total <= 0:
        return "n/a (0 judged)"
    lo, hi = wilson_interval(successes, total, confidence)
    return (
        f"{successes / total * 100:.1f}% ({confidence:.0%} CI {lo * 100:.1f}-{hi * 100:.1f}%, "
        f"{successes}/{total})"
    )


@dataclass
class FisherResult:
    """One 2x2 contingency table and its exact test results.

                Correct   Incorrect
    Condition A    a          b
    Condition B    c          d
    """

    a_correct: int
    a_incorrect: int
    b_correct: int
    b_incorrect: int
    odds_ratio: float
    p_two_sided: float
    p_one_sided_greater: float
    """P(A's odds of being correct > B's) under the null — the directional
    test, appropriate here because the hypothesis under test (grounding
    via a real calc engine + Chaos data should make damage claims MORE
    accurate, never less) was fixed before this data was collected, not
    fit to it after the fact."""
    odds_ratio_ci: tuple[float, float] = (math.nan, math.nan)
    """Exact (conditional maximum-likelihood) 95% interval for the odds ratio."""

    @property
    def a_total(self) -> int:
        return self.a_correct + self.a_incorrect

    @property
    def b_total(self) -> int:
        return self.b_correct + self.b_incorrect

    @property
    def a_rate(self) -> float | None:
        return (self.a_correct / self.a_total) if self.a_total else None

    @property
    def b_rate(self) -> float | None:
        return (self.b_correct / self.b_total) if self.b_total else None

    def summary(self) -> str:
        sig = "significant" if self.p_two_sided < 0.05 else "NOT significant"
        lo, hi = self.odds_ratio_ci
        return (
            f"A: {format_rate(self.a_correct, self.a_total)}  vs  "
            f"B: {format_rate(self.b_correct, self.b_total)}\n"
            f"Fisher's exact test — odds ratio: {self.odds_ratio:.2f} "
            f"(95% CI {lo:.2f}-{hi:.2f}), "
            f"two-sided p={self.p_two_sided:.4f}, one-sided (A>B) p={self.p_one_sided_greater:.4f} "
            f"-> {sig} at alpha=0.05"
        )


def fisher_exact_2x2(a_correct: int, a_incorrect: int, b_correct: int, b_incorrect: int) -> FisherResult:
    """Run Fisher's exact test on the correct/incorrect counts for two
    conditions. Both the two-sided p-value (no directional assumption) and
    the one-sided p-value for the pre-registered directional hypothesis
    (A's correct rate > B's) are computed; report whichever your reader
    expects, but the two-sided figure is the more conservative default."""
    table = [[a_correct, a_incorrect], [b_correct, b_incorrect]]
    odds_ratio, p_two = fisher_exact(table, alternative="two-sided")
    _, p_greater = fisher_exact(table, alternative="greater")
    interval: tuple[float, float] = (math.nan, math.nan)
    if all(sum(row) > 0 for row in table) and all(a + b > 0 for a, b in zip(*table)):
        ci = _odds_ratio(table, kind="conditional").confidence_interval(CONFIDENCE)
        interval = (float(ci.low), float(ci.high))
    return FisherResult(
        a_correct=a_correct,
        a_incorrect=a_incorrect,
        b_correct=b_correct,
        b_incorrect=b_incorrect,
        odds_ratio=float(odds_ratio),
        p_two_sided=float(p_two),
        p_one_sided_greater=float(p_greater),
        odds_ratio_ci=interval,
    )
