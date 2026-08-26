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

from dataclasses import dataclass

from scipy.stats import fisher_exact


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
        a_pct = f"{self.a_rate * 100:.1f}%" if self.a_rate is not None else "n/a"
        b_pct = f"{self.b_rate * 100:.1f}%" if self.b_rate is not None else "n/a"
        sig = "significant" if self.p_two_sided < 0.05 else "NOT significant"
        return (
            f"A: {self.a_correct}/{self.a_total} ({a_pct})  vs  "
            f"B: {self.b_correct}/{self.b_total} ({b_pct})\n"
            f"Fisher's exact test — odds ratio: {self.odds_ratio:.2f}, "
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
    return FisherResult(
        a_correct=a_correct,
        a_incorrect=a_incorrect,
        b_correct=b_correct,
        b_incorrect=b_incorrect,
        odds_ratio=float(odds_ratio),
        p_two_sided=float(p_two),
        p_one_sided_greater=float(p_greater),
    )
