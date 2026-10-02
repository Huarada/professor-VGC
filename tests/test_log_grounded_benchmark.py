"""Tests for the log-grounded benchmark (no LLM, no network).

The original faithfulness benchmark scored damage claims against the
pipeline's own projections — the numbers Condition A was given — which is
circular. These tests pin the pieces that replace that reference with the
damage the battle log shows actually happened.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.faithfulness_benchmark.engine_calibration import calibrate
from scripts.faithfulness_benchmark.log_claims import (
    circularity_matrix,
    verify_damage_claim_against_log,
)
from scripts.faithfulness_benchmark.models import AtomicClaim, ClaimVerdict
from scripts.faithfulness_benchmark.observed_damage import ObservedHit, extract_observed_hits
from src.domain.models import TurnCheck, TurnDamageCheck

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replays"
_BENCHMARK = Path(__file__).resolve().parents[1] / "scripts" / "faithfulness_benchmark"

_LOG = "\n".join([
    "|player|p1|A|", "|player|p2|B|", "|start",
    "|switch|p1a: Chompy|Garchomp, L50|100/100",
    "|switch|p1b: Rillaboom|Rillaboom, L50|100/100",
    "|switch|p2a: Amoonguss|Amoonguss, L50|100/100",
    "|switch|p2b: Gholdengo|Gholdengo, L50|80/100",
    "|turn|1",
    "|move|p1a: Chompy|Earthquake|p2a: Amoonguss|[spread] p1b,p2a,p2b",
    "|-damage|p1b: Rillaboom|90/100",
    "|-damage|p2a: Amoonguss|61/100",
    "|-crit|p2b: Gholdengo",
    "|-damage|p2b: Gholdengo|10/100",
    "|-damage|p2a: Amoonguss|55/100|[from] item: Rocky Helmet",
    "|-heal|p2a: Amoonguss|67/100|[from] item: Sitrus Berry",
    "|move|p2b: Gholdengo|Population Bomb|p1a: Chompy",
    "|-damage|p1a: Chompy|80/100",
    "|-damage|p1a: Chompy|65/100",
    "|-hitcount|p1a: Chompy|2",
    "|upkeep",
    "|turn|2",
    "|move|p1b: Rillaboom|Wood Hammer|p2a: Amoonguss",
    "|-damage|p2a: Amoonguss|0 fnt",
    "|faint|p2a: Amoonguss",
])


def _hit(attacker: str, move: str, defender: str) -> ObservedHit:
    hits = extract_observed_hits(_LOG)
    return next(
        h for h in hits if (h.attacker, h.move, h.defender) == (attacker, move, defender)
    )


# -- observed damage (the external truth) --------------------------------------


def test_reference_reader_is_independent_of_the_product():
    tree = ast.parse((_BENCHMARK / "observed_damage.py").read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name.startswith("src") for name in imported), imported


def test_spread_hits_crit_and_identity_by_species():
    ally = _hit("Garchomp", "Earthquake", "Rillaboom")  # nickname resolved to species
    assert (ally.hp_before, ally.hp_after, ally.damage) == (100.0, 90.0, 10.0)
    assert ally.spread and ally.clean
    crit = _hit("Garchomp", "Earthquake", "Gholdengo")
    assert crit.crit and not crit.clean
    assert crit.hp_before == 80.0 and crit.damage == 70.0  # started below full HP


def test_residual_damage_is_not_a_hit_but_moves_the_hp():
    eq = _hit("Garchomp", "Earthquake", "Amoonguss")
    assert eq.damage == 39.0  # Rocky Helmet chip afterwards is not part of the hit
    ko = _hit("Rillaboom", "Wood Hammer", "Amoonguss")
    assert ko.hp_before == 67.0  # after the Sitrus Berry heal
    assert ko.fainted and ko.damage == 67.0  # a KO only proves a lower bound


def test_multi_hit_moves_are_flagged():
    bomb = _hit("Gholdengo", "Population Bomb", "Garchomp")
    assert bomb.hit_count == 2 and not bomb.clean and bomb.damage == 35.0


@pytest.mark.parametrize("path", sorted(_FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_real_replay_fixtures_are_anonymized_and_readable(path: Path):
    replay = json.loads(path.read_text(encoding="utf-8"))
    assert replay["players"] == ["Player A", "Player B"]
    assert "|c|" not in replay["log"]  # no chat in versioned data
    hits = extract_observed_hits(replay["log"])
    assert hits and any(h.fainted for h in hits) and any(h.crit for h in hits)
    assert all(0.0 <= h.hp_after <= h.hp_before <= 100.0 for h in hits)


def test_known_hit_in_a_real_replay():
    replay = json.loads(
        (_FIXTURES / "gen9championsvgc2026regmb-2691463439.json").read_text(encoding="utf-8")
    )
    hits = extract_observed_hits(replay["log"])
    gleam = [h for h in hits if h.move == "Dazzling Gleam"]
    assert [(h.defender, h.hp_before, h.hp_after) for h in gleam] == [
        ("Diggersby", 90.0, 71.0), ("Noivern", 100.0, 56.0),
    ]
    assert all(h.spread for h in gleam)


# -- engine calibration ---------------------------------------------------------


def _check(attacker: str, move: str, target: str, lo: float, hi: float, turn: int = 1) -> TurnCheck:
    players = {"Garchomp": "p1", "Rillaboom": "p1", "Gholdengo": "p2", "Amoonguss": "p2"}
    return TurnCheck(
        turn=turn, actor=attacker, actor_player=players[attacker], move=move,
        damage_checks=[TurnDamageCheck(
            target=target, target_player=players[target],
            projected_min_percent=lo, projected_max_percent=hi,
        )],
    )


def test_calibration_judges_each_hit_against_its_own_projection():
    checks = [
        _check("Garchomp", "Earthquake", "Rillaboom", 8.0, 9.5),  # observed 10 (within ±2)
        _check("Garchomp", "Earthquake", "Amoonguss", 20.0, 25.0),  # observed 39: wrong
        _check("Garchomp", "Earthquake", "Gholdengo", 20.0, 25.0),  # crit: excluded
        _check("Rillaboom", "Wood Hammer", "Amoonguss", 70.0, 80.0, turn=2),  # KO from 67
    ]
    report = calibrate(extract_observed_hits(_LOG), checks)
    outcomes = {(h.hit.attacker, h.hit.move, h.hit.defender): h.outcome for h in report.hits}
    assert outcomes[("Garchomp", "Earthquake", "Rillaboom")] == "correct"
    assert outcomes[("Garchomp", "Earthquake", "Amoonguss")] == "incorrect"
    assert outcomes[("Garchomp", "Earthquake", "Gholdengo")] == "excluded"
    assert outcomes[("Rillaboom", "Wood Hammer", "Amoonguss")] == "correct"
    assert outcomes[("Gholdengo", "Population Bomb", "Garchomp")] == "unmatched"
    wrong = next(h for h in report.hits if h.outcome == "incorrect")
    assert wrong.error_pp == 14.0  # 39 observed vs 25 max: under-projected by 14pp
    assert report.summary()["coverage_rate_non_ko"] == 0.5


def test_a_ko_the_projection_cannot_reach_is_incorrect():
    checks = [_check("Rillaboom", "Wood Hammer", "Amoonguss", 30.0, 40.0, turn=2)]
    report = calibrate(extract_observed_hits(_LOG), checks)
    ko = next(h for h in report.hits if h.hit.fainted)
    assert ko.outcome == "incorrect" and "KO from 67.0%" in ko.reason


# -- claims vs the log ----------------------------------------------------------


def _claim(attacker: str, move: str, defender: str, lo: float, hi: float) -> AtomicClaim:
    return AtomicClaim(
        claim_type="damage_range", attacker=attacker, defender=defender, move=move,
        min_percent=lo, max_percent=hi,
    )


def test_claims_are_judged_against_observed_damage():
    hits = extract_observed_hits(_LOG)
    right = verify_damage_claim_against_log(_claim("p1 Garchomp", "Earthquake", "Amoonguss", 35, 40), hits)
    wrong = verify_damage_claim_against_log(_claim("Garchomp", "Earthquake", "Amoonguss", 15, 20), hits)
    absent = verify_damage_claim_against_log(_claim("Garchomp", "Dragon Claw", "Amoonguss", 30, 35), hits)
    ko = verify_damage_claim_against_log(_claim("Rillaboom", "Wood Hammer", "Amoonguss", 70, 85), hits)
    short = verify_damage_claim_against_log(_claim("Rillaboom", "Wood Hammer", "Amoonguss", 40, 50), hits)
    assert [v.verdict for v in (right, wrong, absent, ko, short)] == [
        "correct", "incorrect", "not_in_log", "correct", "incorrect",
    ]
    assert "observed T1 39.0%" in wrong.reason


def test_circularity_matrix_counts_projection_vs_log_disagreements():
    claims = [_claim("Garchomp", "Earthquake", "Amoonguss", 20, 25)] * 2
    projection = [ClaimVerdict(claim=c, verdict="correct") for c in claims]
    log = [verify_damage_claim_against_log(c, extract_observed_hits(_LOG)) for c in claims]
    assert circularity_matrix(projection, log) == {"correct -> incorrect": 2}
