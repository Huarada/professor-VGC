"""Score (and re-score) a log-grounded run under any tolerance band.

A saved run keeps every extracted claim, so changing the tolerance never
needs new LLM calls: claims are re-judged against the observed damage of each
replay. Every rate is reported with a 95% Wilson interval and the A-vs-B
comparison with Fisher's exact test plus the odds ratio's 95% interval, at
the chosen band and across ``SENSITIVITY_BANDS``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from scripts.faithfulness_benchmark.log_claims import verify_damage_claim_against_log
from scripts.faithfulness_benchmark.models import AtomicClaim
from scripts.faithfulness_benchmark.observed_damage import ObservedHit
from scripts.faithfulness_benchmark.stats import fisher_exact_2x2, format_rate
from scripts.faithfulness_benchmark.tolerance import SENSITIVITY_BANDS, ToleranceBand

LABELS = ("A_grounded", "B_naive")
VERDICTS = ("correct", "incorrect", "not_in_log", "unverifiable")


def rescore(
    results: list[dict[str, Any]],
    hits_by_replay: dict[str, list[ObservedHit]],
    band: ToleranceBand,
) -> tuple[dict[str, Counter[str]], Counter[str]]:
    """Verdict counts per condition and the A circularity matrix under ``band``."""
    totals: dict[str, Counter[str]] = {label: Counter() for label in LABELS}
    circularity: Counter[str] = Counter()
    for result in results:
        hits = hits_by_replay.get(result["replay_id"], [])
        for label in LABELS:
            for stored in result[label]["claims"]:
                verdict = verify_damage_claim_against_log(
                    AtomicClaim.model_validate(stored["claim"]), hits, band
                ).verdict
                totals[label][verdict] += 1
                projection = stored.get("projection_verdict")
                if label == "A_grounded" and projection:
                    circularity[f"{projection} -> {verdict}"] += 1
        if not any("projection_verdict" in c for c in result["A_grounded"]["claims"]):
            circularity.update(result["A_grounded"].get("circularity", {}))
    return totals, circularity


def _comparison(totals: dict[str, Counter[str]]) -> dict[str, str]:
    a, b = totals["A_grounded"], totals["B_naive"]
    fisher = fisher_exact_2x2(a["correct"], a["incorrect"], b["correct"], b["incorrect"])
    lo, hi = fisher.odds_ratio_ci
    return {
        "A_grounded": format_rate(a["correct"], a["correct"] + a["incorrect"]),
        "B_naive": format_rate(b["correct"], b["correct"] + b["incorrect"]),
        "odds_ratio": f"{fisher.odds_ratio:.2f} (95% CI {lo:.2f}-{hi:.2f})",
        "p_two_sided": f"{fisher.p_two_sided:.4f}",
    }


def summarize(
    results: list[dict[str, Any]],
    hits_by_replay: dict[str, list[ObservedHit]],
    band: ToleranceBand,
) -> dict[str, Any]:
    totals, circularity = rescore(results, hits_by_replay, band)
    return {
        "replays": len(results),
        "tolerance_band": band.label,
        "A_grounded": {v: totals["A_grounded"][v] for v in VERDICTS},
        "B_naive": {v: totals["B_naive"][v] for v in VERDICTS},
        "comparison_95ci": _comparison(totals),
        "fisher_exact_on_log_verified_damage_claims": fisher_exact_2x2(
            totals["A_grounded"]["correct"], totals["A_grounded"]["incorrect"],
            totals["B_naive"]["correct"], totals["B_naive"]["incorrect"],
        ).summary(),
        "sensitivity_by_relative_tolerance": {
            other.label: _comparison(rescore(results, hits_by_replay, other)[0])
            for other in SENSITIVITY_BANDS
        },
        "A_projection_vs_log_circularity": dict(sorted(circularity.items())),
    }
