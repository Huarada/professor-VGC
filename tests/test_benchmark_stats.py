"""Unit tests for scripts/faithfulness_benchmark/stats.py's Fisher's exact
test wrapper — no LLM, no network."""

from __future__ import annotations

from scripts.faithfulness_benchmark.stats import fisher_exact_2x2


def test_identical_proportions_are_not_significant():
    result = fisher_exact_2x2(a_correct=5, a_incorrect=5, b_correct=5, b_incorrect=5)
    assert result.p_two_sided == 1.0
    assert result.a_rate == 0.5
    assert result.b_rate == 0.5


def test_a_clear_gap_is_significant():
    # A: 9/10 correct, B: 1/10 correct -- a stark, unambiguous difference.
    result = fisher_exact_2x2(a_correct=9, a_incorrect=1, b_correct=1, b_incorrect=9)
    assert result.p_two_sided < 0.05
    assert result.p_one_sided_greater < 0.05
    assert result.odds_ratio > 1.0


def test_a_modest_gap_at_small_n_may_not_reach_significance():
    # A: 6/11, B: 1/18 -- this benchmark's actual pre-densification numbers.
    # Included to document, not assert a specific p-value (scipy's exact
    # computation is the source of truth) -- just that the wrapper runs
    # end to end on a real small-n case without raising.
    result = fisher_exact_2x2(a_correct=6, a_incorrect=5, b_correct=1, b_incorrect=17)
    assert 0.0 <= result.p_two_sided <= 1.0
    assert result.a_total == 11
    assert result.b_total == 18


def test_zero_total_rate_is_none_not_a_crash():
    result = fisher_exact_2x2(a_correct=0, a_incorrect=0, b_correct=3, b_incorrect=2)
    assert result.a_rate is None
    assert result.b_rate == 0.6


def test_summary_reports_significance_flag():
    sig = fisher_exact_2x2(a_correct=9, a_incorrect=1, b_correct=1, b_incorrect=9)
    not_sig = fisher_exact_2x2(a_correct=5, a_incorrect=5, b_correct=5, b_incorrect=5)
    assert "NOT significant" not in sig.summary()
    assert "NOT significant" in not_sig.summary()
