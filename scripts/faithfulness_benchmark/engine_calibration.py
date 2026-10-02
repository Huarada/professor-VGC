"""How well do the pipeline's PROJECTED damage ranges match what really happened?

Compares every observed hit (``observed_damage.py``, read straight from the
log) with the range the deterministic layer projected for that same hit
(``TurnCheck.damage_checks``). No LLM is involved: this measures the ground
truth itself, which the original benchmark took for granted. A projection
can only be as good as its assumptions — unrevealed EVs/natures/items are
backed off the most-used Chaos spread — and this quantifies that gap.

Judging rules per observed hit:

- clean, not KO: ``correct`` when the observed damage lies inside the
  projected range widened by ``tolerance`` (HP-display rounding);
- clean, KO: the log only proves damage >= the HP the target had, so it is
  ``correct`` when the projected max (+ tolerance) reaches that HP;
- crits and multi-hit moves are ``excluded`` (no single calc range models
  them) and counted separately, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from scripts.faithfulness_benchmark.observed_damage import HP_ROUNDING_PP, ObservedHit
from src.domain.models import TurnCheck

DEFAULT_TOLERANCE_PP = 2 * HP_ROUNDING_PP  # a damage value is the difference of two readings

Outcome = Literal["correct", "incorrect", "excluded", "unmatched"]


@dataclass
class HitCalibration:
    hit: ObservedHit
    outcome: Outcome
    projected_min: float | None = None
    projected_max: float | None = None
    reason: str = ""

    @property
    def error_pp(self) -> float | None:
        """Distance from the observed damage to the projected range (0 inside it;
        negative = the engine over-projected). Only for clean, non-KO hits."""
        if self.projected_min is None or self.projected_max is None:
            return None
        if self.hit.fainted or not self.hit.clean:
            return None
        observed = self.hit.damage
        if observed < self.projected_min:
            return round(observed - self.projected_min, 1)
        if observed > self.projected_max:
            return round(observed - self.projected_max, 1)
        return 0.0


@dataclass
class CalibrationReport:
    hits: list[HitCalibration] = field(default_factory=list)

    def count(self, outcome: Outcome, *, ko: bool | None = None) -> int:
        return sum(
            1 for h in self.hits
            if h.outcome == outcome and (ko is None or h.hit.fainted == ko)
        )

    def rate(self, *, ko: bool | None = None) -> float | None:
        correct = self.count("correct", ko=ko)
        judged = correct + self.count("incorrect", ko=ko)
        return correct / judged if judged else None

    def mean_abs_error(self) -> float | None:
        errors = [abs(e) for h in self.hits if (e := h.error_pp) is not None]
        return round(sum(errors) / len(errors), 2) if errors else None

    def summary(self) -> dict[str, object]:
        return {
            "observed_hits": len(self.hits),
            "judged": self.count("correct") + self.count("incorrect"),
            "correct": self.count("correct"),
            "incorrect": self.count("incorrect"),
            "excluded_crit_or_multihit": self.count("excluded"),
            "unmatched_no_projection": self.count("unmatched"),
            "coverage_rate_all": self.rate(),
            "coverage_rate_non_ko": self.rate(ko=False),
            "consistency_rate_ko": self.rate(ko=True),
            "mean_abs_error_outside_range_pp": self.mean_abs_error(),
        }


def _projection_index(
    checks: list[TurnCheck],
) -> dict[tuple[int, str, str, str, str, str], tuple[float, float]]:
    index: dict[tuple[int, str, str, str, str, str], tuple[float, float]] = {}
    for check in checks:
        for dmg in check.damage_checks:
            key = (check.turn, check.actor_player, check.actor, check.move,
                   dmg.target_player, dmg.target)
            index.setdefault(key, (dmg.projected_min_percent, dmg.projected_max_percent))
    return index


def calibrate(
    hits: list[ObservedHit],
    checks: list[TurnCheck],
    tolerance: float = DEFAULT_TOLERANCE_PP,
) -> CalibrationReport:
    """Judge every observed hit against the projection made for that same hit."""
    index = _projection_index(checks)
    report = CalibrationReport()
    for hit in hits:
        key = (hit.turn, hit.attacker_player, hit.attacker, hit.move,
               hit.defender_player, hit.defender)
        projected = index.get(key)
        if projected is None:
            report.hits.append(HitCalibration(hit, "unmatched", reason="no projection for this hit"))
            continue
        lo, hi = projected
        if not hit.clean:
            report.hits.append(
                HitCalibration(hit, "excluded", lo, hi, reason="critical hit or multi-hit move")
            )
            continue
        if hit.fainted:
            ok = hi + tolerance >= hit.hp_before
            reason = "" if ok else f"KO from {hit.hp_before}% but projected max {hi}%"
        else:
            ok = lo - tolerance <= hit.damage <= hi + tolerance
            reason = "" if ok else f"observed {hit.damage}% vs projected {lo}-{hi}%"
        report.hits.append(HitCalibration(hit, "correct" if ok else "incorrect", lo, hi, reason))
    return report
