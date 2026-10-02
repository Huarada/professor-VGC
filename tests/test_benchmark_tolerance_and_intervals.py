"""Tests for tolerance bands, confidence intervals, the EV/nature envelope
and re-scoring of saved log-grounded runs (no LLM, no network).

Reported: a single exact-match criterion ("acerto certeiro") overstates the
precision of a damage comparison — the real Pokemon's EVs/nature differ from
the one assumed spread, and HP is displayed rounded. Agreement is now judged
within a stated band, every rate carries a 95% interval, and the same data is
re-scored across several tolerances.
"""

from __future__ import annotations

import json
import math

import pytest

from scripts.faithfulness_benchmark.engine_calibration import calibrate, hit_key
from scripts.faithfulness_benchmark.ev_envelope import RecordingCalc, ev_envelope, envelopes_for
from scripts.faithfulness_benchmark.log_claims import verify_damage_claim_against_log
from scripts.faithfulness_benchmark.log_grounded_scoring import summarize
from scripts.faithfulness_benchmark.models import AtomicClaim
from scripts.faithfulness_benchmark.observed_damage import ObservedHit
from scripts.faithfulness_benchmark.stats import fisher_exact_2x2, format_rate, wilson_interval
from scripts.faithfulness_benchmark.tolerance import EXACT_BAND, ToleranceBand
from src.domain.models import CalcRequest, DamageResult, PokemonSet, TurnCheck, TurnDamageCheck
from tests.conftest import FakeCalcEngine


def _hit(damage: float, *, hp_before: float = 100.0, fainted: bool = False) -> ObservedHit:
    return ObservedHit(
        turn=1, attacker="Garchomp", attacker_player="p1", move="Earthquake",
        defender="Amoonguss", defender_player="p2", hp_before=hp_before,
        hp_after=0.0 if fainted else hp_before - damage, fainted=fainted,
    )


def _check(lo: float, hi: float) -> TurnCheck:
    return TurnCheck(
        turn=1, actor="Garchomp", actor_player="p1", move="Earthquake",
        damage_checks=[TurnDamageCheck(
            target="Amoonguss", target_player="p2",
            projected_min_percent=lo, projected_max_percent=hi,
        )],
    )


# -- tolerance band --------------------------------------------------------------


def test_band_widens_absolutely_and_relatively():
    band = ToleranceBand(absolute_pp=2.0, relative=0.05)
    assert band.widen(40.0, 50.0) == pytest.approx((36.0, 54.5))
    assert band.contains(40.0, 50.0, 54.0) and not band.contains(40.0, 50.0, 55.0)
    assert band.reaches(60.0, 65.0) and not band.reaches(60.0, 66.0)
    assert EXACT_BAND.widen(40.0, 50.0) == pytest.approx((38.0, 52.0))


def test_a_hit_inside_the_relative_band_is_no_longer_a_miss():
    hit = [_hit(53.0)]  # vs 40-48: upper bound 50.0 (±2pp), 52.4 (+5%), 54.8 (+10%)
    exact = calibrate(hit, [_check(40.0, 48.0)], EXACT_BAND)
    five = calibrate(hit, [_check(40.0, 48.0)], ToleranceBand(relative=0.05))
    ten = calibrate(hit, [_check(40.0, 48.0)], ToleranceBand(relative=0.10))
    assert [r.hits[0].outcome for r in (exact, five, ten)] == ["incorrect", "incorrect", "correct"]
    assert ten.hits[0].error_pp == 5.0  # error is always against the RAW projected range


def test_rescoring_reuses_the_stored_projection():
    report = calibrate([_hit(53.0)], [_check(40.0, 48.0)], EXACT_BAND)
    assert report.rescored(ToleranceBand(relative=0.10)).hits[0].outcome == "correct"
    sensitivity = report.summary()["sensitivity_by_relative_tolerance"]
    assert set(sensitivity) == {"±2pp ±0%", "±2pp ±5%", "±2pp ±10%", "±2pp ±15%"}


def test_claims_use_the_same_band():
    claim = AtomicClaim(
        claim_type="damage_range", attacker="Garchomp", defender="Amoonguss",
        move="Earthquake", min_percent=40, max_percent=48,
    )
    hits = [_hit(53.0)]
    assert verify_damage_claim_against_log(claim, hits, EXACT_BAND).verdict == "incorrect"
    assert verify_damage_claim_against_log(claim, hits, ToleranceBand(relative=0.10)).verdict == "correct"


# -- confidence intervals ---------------------------------------------------------


def test_wilson_interval_brackets_the_rate_and_handles_the_edges():
    lo, hi = wilson_interval(110, 155)
    assert lo < 110 / 155 < hi
    assert (round(lo, 3), round(hi, 3)) == (0.634, 0.775)
    assert wilson_interval(0, 10)[0] == 0.0 and wilson_interval(10, 10)[1] == 1.0
    assert all(math.isnan(x) for x in wilson_interval(0, 0))
    assert format_rate(110, 155) == "71.0% (95% CI 63.4-77.5%, 110/155)"


def test_fisher_result_carries_the_odds_ratio_interval():
    result = fisher_exact_2x2(110, 45, 96, 77)
    lo, hi = result.odds_ratio_ci
    assert lo < result.odds_ratio < hi and lo > 1.0  # interval excludes "no effect"
    assert "95% CI" in result.summary()


# -- EV / nature envelope ---------------------------------------------------------


class _SpreadAwareCalc(FakeCalcEngine):
    """Damage grows with attacker investment and shrinks with defender bulk."""

    def calculate(self, request: CalcRequest) -> DamageResult:
        evs_a = request.attacker.evs.atk if request.attacker.evs else 0
        bulk = (request.defender.evs.hp + request.defender.evs.def_) if request.defender.evs else 0
        pct = 40.0 + evs_a / 252 * 10 - bulk / 504 * 10
        return DamageResult(
            attacker=request.attacker.species, defender=request.defender.species,
            move=request.move, damage_rolls=[1], min_percent=pct * 0.85, max_percent=pct,
        )


def test_ev_envelope_spans_minimum_to_maximum_investment():
    request = CalcRequest(
        attacker=PokemonSet(species="Garchomp"), defender=PokemonSet(species="Amoonguss"),
        move="Earthquake",
    )
    lo, hi = ev_envelope(_SpreadAwareCalc(), request) or (0.0, 0.0)
    assert lo == pytest.approx(30.0 * 0.85) and hi == pytest.approx(50.0)
    assert ev_envelope(_SpreadAwareCalc(), request.model_copy(update={"move": "Protect"})) is None


def test_misses_are_split_into_explained_and_unexplained_by_ev_variance():
    engine = _SpreadAwareCalc()
    recorder = RecordingCalc(engine)
    recorder.calculate(CalcRequest(
        attacker=PokemonSet(species="Garchomp"), defender=PokemonSet(species="Amoonguss"),
        move="Earthquake", defender_hp_percent=100.0,
    ))
    explained, unexplained = _hit(47.0), _hit(70.0)
    report = calibrate(
        [explained], [_check(30.0, 35.0)], EXACT_BAND, envelopes_for([explained], recorder, engine),
    )
    assert report.hits[0].outcome == "incorrect" and report.hits[0].within_ev_envelope is True
    report = calibrate(
        [unexplained], [_check(30.0, 35.0)], EXACT_BAND,
        {hit_key(unexplained): (25.5, 50.0)},
    )
    assert report.hits[0].within_ev_envelope is False
    assert report.summary()["misses_vs_ev_nature_envelope"]["outside_any_ev_nature_spread"] == 1


# -- re-scoring a saved run -------------------------------------------------------


def test_saved_claims_are_rescored_without_new_llm_calls():
    def claim(lo: float, hi: float) -> dict[str, object]:
        return {"claim": AtomicClaim(
            claim_type="damage_range", attacker="Garchomp", defender="Amoonguss",
            move="Earthquake", min_percent=lo, max_percent=hi,
        ).model_dump(), "projection_verdict": "correct"}

    results = [{
        "replay_id": "r1",
        "A_grounded": {"claims": [claim(40, 48), claim(50, 55)]},
        "B_naive": {"claims": [claim(10, 12)]},
    }]
    hits = {"r1": [_hit(53.0)]}
    exact = summarize(results, hits, EXACT_BAND)
    assert exact["A_grounded"]["correct"] == 1 and exact["A_grounded"]["incorrect"] == 1
    assert exact["A_projection_vs_log_circularity"] == {"correct -> correct": 1, "correct -> incorrect": 1}
    ten = summarize(results, hits, ToleranceBand(relative=0.10))
    assert ten["A_grounded"]["correct"] == 2
    assert ten["comparison_95ci"]["A_grounded"].startswith("100.0%")


# -- distinct games per run / pooling ---------------------------------------------


def test_offset_selects_different_games(tmp_path):
    from scripts.faithfulness_benchmark.replay_corpus import load_replays

    for i in range(4):
        (tmp_path / f"g{i}.json").write_text(json.dumps({"id": f"g{i}", "log": "|"}), encoding="utf-8")
    first = load_replays(tmp_path, limit=2)
    second = load_replays(tmp_path, limit=2, offset=2)
    assert list(first) == ["g0", "g1"] and list(second) == ["g2", "g3"]


def test_pooling_refuses_reports_that_share_a_game(tmp_path, monkeypatch):
    from scripts.faithfulness_benchmark import rescore_log_grounded

    report = {"replays": [{"replay_id": "g1", "A_grounded": {"claims": []}, "B_naive": {"claims": []}}]}
    paths = []
    for name in ("a.json", "b.json"):
        path = tmp_path / name
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(str(path))
    monkeypatch.setattr("sys.argv", ["rescore", *paths, "--replays", str(tmp_path)])
    with pytest.raises(SystemExit, match="share games"):
        rescore_log_grounded.main()
