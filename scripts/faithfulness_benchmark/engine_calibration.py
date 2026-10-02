"""How well do the pipeline's PROJECTED damage ranges match what really happened?

Compares every observed hit (``observed_damage.py``, read straight from the
log) with the range the deterministic layer projected for that same hit
(``TurnCheck.damage_checks``). No LLM is involved: this measures the ground
truth itself. A projection can only be as good as its assumptions —
unrevealed EVs/natures/items are backed off the most-used Chaos spread — and
this quantifies that gap.

Judging rules per observed hit, under a :class:`ToleranceBand` (default
±2pp HP rounding and ±5% for spread variance):

- clean, not KO: ``correct`` when the observed damage lies inside the widened
  projected range;
- clean, KO: the log only proves damage >= the HP the target had, so it is
  ``correct`` when the widened projected max reaches that HP;
- crits and multi-hit moves are ``excluded`` (no single calc range models
  them) and counted separately, never silently dropped.

Every rate is reported with a 95% Wilson interval, and re-scored at several
relative tolerances (``sensitivity``). Optionally each hit also carries an
**EV envelope** — the range the same hit could do from minimum to maximum
EV/nature investment on both sides, everything else equal — which tells
how many misses EV/nature variance alone can explain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from scripts.faithfulness_benchmark.observed_damage import ObservedHit
from scripts.faithfulness_benchmark.stats import format_rate, wilson_interval
from scripts.faithfulness_benchmark.tolerance import (
    DEFAULT_BAND,
    EXACT_BAND,
    SENSITIVITY_BANDS,
    ToleranceBand,
)
from src.domain.models import TurnCheck

Outcome = Literal["correct", "incorrect", "excluded", "unmatched"]
HitKey = tuple[int, str, str, str, str, str]  # turn, attacker side/species, move, defender side/species


def hit_key(hit: ObservedHit) -> HitKey:
    return (hit.turn, hit.attacker_player, hit.attacker, hit.move,
            hit.defender_player, hit.defender)


@dataclass
class HitCalibration:
    hit: ObservedHit
    outcome: Outcome
    projected_min: float | None = None
    projected_max: float | None = None
    reason: str = ""
    ev_envelope: tuple[float, float] | None = None
    """Damage range from minimum to maximum EV/nature investment (None = not computed)."""

    @property
    def error_pp(self) -> float | None:
        """Distance from the observed damage to the RAW projected range (0 inside it;
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

    @property
    def within_ev_envelope(self) -> bool | None:
        """Whether EV/nature variance alone can explain the observed damage
        (rounding tolerance only; None when no envelope was computed)."""
        if self.ev_envelope is None or not self.hit.clean:
            return None
        lo, hi = self.ev_envelope
        if self.hit.fainted:
            return EXACT_BAND.reaches(hi, self.hit.hp_before)
        return EXACT_BAND.contains(lo, hi, self.hit.damage)


def judge(
    hit: ObservedHit, projected: tuple[float, float] | None, band: ToleranceBand
) -> tuple[Outcome, str]:
    """One hit's verdict under ``band`` (pure; used for re-scoring too)."""
    if projected is None:
        return "unmatched", "no projection for this hit"
    if not hit.clean:
        return "excluded", "critical hit or multi-hit move"
    lo, hi = projected
    if hit.fainted:
        if band.reaches(hi, hit.hp_before):
            return "correct", ""
        return "incorrect", f"KO from {hit.hp_before}% but projected max {hi}%"
    if band.contains(lo, hi, hit.damage):
        return "correct", ""
    return "incorrect", f"observed {hit.damage}% vs projected {lo}-{hi}%"


@dataclass
class CalibrationReport:
    band: ToleranceBand = DEFAULT_BAND
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

    def rate_text(self, *, ko: bool | None = None) -> str:
        correct = self.count("correct", ko=ko)
        return format_rate(correct, correct + self.count("incorrect", ko=ko))

    def rate_interval(self, *, ko: bool | None = None) -> tuple[float, float]:
        correct = self.count("correct", ko=ko)
        return wilson_interval(correct, correct + self.count("incorrect", ko=ko))

    def mean_abs_error(self) -> float | None:
        errors = [abs(e) for h in self.hits if (e := h.error_pp) is not None and e != 0.0]
        return round(sum(errors) / len(errors), 2) if errors else None

    def rescored(self, band: ToleranceBand) -> "CalibrationReport":
        """The same hits and projections, judged under another band."""
        report = CalibrationReport(band=band)
        for h in self.hits:
            projected = (
                (h.projected_min, h.projected_max)
                if h.projected_min is not None and h.projected_max is not None else None
            )
            outcome, reason = judge(h.hit, projected, band)
            report.hits.append(HitCalibration(
                h.hit, outcome, h.projected_min, h.projected_max, reason, h.ev_envelope,
            ))
        return report

    def sensitivity(self) -> dict[str, dict[str, str]]:
        return {
            band.label: {
                "non_ko": self.rescored(band).rate_text(ko=False),
                "ko": self.rescored(band).rate_text(ko=True),
            }
            for band in SENSITIVITY_BANDS
        }

    def ev_explained(self) -> dict[str, object] | None:
        misses = [h for h in self.hits if h.outcome == "incorrect" and h.within_ev_envelope is not None]
        if not misses:
            return None
        explained = sum(1 for h in misses if h.within_ev_envelope)
        return {
            "misses_with_envelope": len(misses),
            "explained_by_ev_nature_variance": format_rate(explained, len(misses)),
            "outside_any_ev_nature_spread": len(misses) - explained,
        }

    def summary(self) -> dict[str, object]:
        result: dict[str, object] = {
            "tolerance_band": self.band.label,
            "observed_hits": len(self.hits),
            "judged": self.count("correct") + self.count("incorrect"),
            "correct": self.count("correct"),
            "incorrect": self.count("incorrect"),
            "excluded_crit_or_multihit": self.count("excluded"),
            "unmatched_no_projection": self.count("unmatched"),
            "coverage_rate_all": self.rate(),
            "coverage_rate_non_ko": self.rate(ko=False),
            "consistency_rate_ko": self.rate(ko=True),
            "coverage_non_ko_95ci": self.rate_text(ko=False),
            "consistency_ko_95ci": self.rate_text(ko=True),
            "mean_abs_error_outside_raw_range_pp": self.mean_abs_error(),
            "sensitivity_by_relative_tolerance": self.sensitivity(),
        }
        explained = self.ev_explained()
        if explained is not None:
            result["misses_vs_ev_nature_envelope"] = explained
        return result


def _projection_index(checks: list[TurnCheck]) -> dict[HitKey, tuple[float, float]]:
    index: dict[HitKey, tuple[float, float]] = {}
    for check in checks:
        for dmg in check.damage_checks:
            key = (check.turn, check.actor_player, check.actor, check.move,
                   dmg.target_player, dmg.target)
            index.setdefault(key, (dmg.projected_min_percent, dmg.projected_max_percent))
    return index


def calibrate(
    hits: list[ObservedHit],
    checks: list[TurnCheck],
    band: ToleranceBand = DEFAULT_BAND,
    ev_envelopes: dict[HitKey, tuple[float, float]] | None = None,
) -> CalibrationReport:
    """Judge every observed hit against the projection made for that same hit."""
    index = _projection_index(checks)
    report = CalibrationReport(band=band)
    for hit in hits:
        projected = index.get(hit_key(hit))
        outcome, reason = judge(hit, projected, band)
        lo, hi = projected if projected is not None else (None, None)
        report.hits.append(HitCalibration(
            hit, outcome, lo, hi, reason,
            (ev_envelopes or {}).get(hit_key(hit)),
        ))
    return report
