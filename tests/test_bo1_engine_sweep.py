"""The simulated-game sweep: game loading and the true/false faint statements built from a log."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.bo1_engine_sweep import load_games, summarize, truth_probes
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest
from src.services.faint_timing import faint_timing_issues
from src.services.ground_truth import parse_replay

_REPLAY = Path(__file__).parent / "fixtures" / "replays" / "gen9championsvgc2026regmb-2691404852.json"


def test_load_games_spreads_a_limit_over_all_series(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        games = [{"log": f"{name}{n}"} for n in range(3)]
        (tmp_path / f"bo3-public-{name}.json").write_text(json.dumps({"games": games}), encoding="utf-8")
    everything = load_games(tmp_path, 0)
    assert len(everything) == 9 and everything[0][0] == "a-g1"
    picked = load_games(tmp_path, 3)
    assert [g[0] for g in picked] == ["a-g1", "b-g1", "c-g1"]


def test_probes_from_a_recorded_log_are_true_or_false_as_labelled() -> None:
    replay = json.loads(_REPLAY.read_text(encoding="utf-8"))
    state = parse_replay(ShowdownReplayParser(), AnalysisRequest(session_id="s", replay_json=replay, question="q"))
    true, false = truth_probes(state)
    assert true and false
    assert not any(faint_timing_issues(text, state) for text in true)
    assert all(faint_timing_issues(text, state) for text in false)


def test_summary_counts_problems_and_validator_rates() -> None:
    rows = [{"id": "a", "problems": [], "seconds": 1.0, "true_probes": 4, "false_probes": 2, "turning_rule": "largest_swing"},
            {"id": "b", "problems": ["x"], "seconds": 3.0, "true_probes": 1, "false_probes": 1, "false_positives": 1,
             "missed_lies": 1, "turning_rule": "lead_held_to_the_end"}, {"id": "c", "problems": ["exception"]}]
    summary = summarize(rows)
    assert (summary["games"], summary["finished"], summary["with_problems"]) == (3, 2, 2)
    assert summary["validator_true_probes"] == 5 and summary["validator_false_positives"] == 1
    assert summary["validator_missed_lies"] == 1 and summary["seconds_mean"] == 2.0
