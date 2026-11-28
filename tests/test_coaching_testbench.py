"""The coaching testbench's pure parts: case list, deterministic checks, aggregation and report."""

from __future__ import annotations

import json
from typing import Any

from scripts import coaching_testbench as bench


def test_cases_cover_bo1_and_bo3_with_existing_fixtures() -> None:
    cases = bench.build_cases()
    assert len({c.id for c in cases}) == len(cases)
    assert {c.mode for c in cases} == {"bo1", "bo3"}
    assert all(c.games and c.question in bench.QUESTIONS for c in cases)
    assert all(len(c.games) == 1 for c in cases if c.mode == "bo1")
    assert all(2 <= len(c.games) <= 3 for c in cases if c.mode == "bo3")
    quick = bench.build_cases(quick=True)
    assert {c.mode for c in quick} == {"bo1", "bo3"} and len(quick) < len(cases)


def test_structure_and_turning_point_checks() -> None:
    answer = "### Result\nA won.\n### Turning Point\nOn turn 11 it swung.\n## Decisions\nx\n**What to change**\ny"
    assert bench.sections_present(answer) == ["result", "turning point", "decisions", "what to change"]
    assert bench.sections_present("Just prose about turn 11.") == []
    assert bench.presents_turning_point(answer, 11, []) is True
    assert bench.presents_turning_point(answer, 12, []) is False
    assert bench.presents_turning_point("The turning point was turn 5; turn 11 only closed it out.", 11, []) is False
    assert bench.presents_turning_point("Turn 11 was the finish.", 11, []) is None  # no turning-point claim
    assert bench.presents_turning_point(answer, None, []) is None


def test_figures_outside_evidence_and_advice_counting() -> None:
    assert bench.figures_outside_evidence("It dealt 45% and 12.5%.", '{"max": "45%"}') == ["12.5%"]
    assert bench.figures_outside_evidence("No numbers here.", "45%") == []
    text = "You should protect. Consider Tailwind. The game ended.\nTry a different lead."
    assert bench.advice_sentences(text) == 3


def _row(**override: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "bo1-x-turning", "mode": "bo1", "question": "q", "source": "s", "seconds": 10.0, "answer": "line one\n\nline two",
        "sections": ["result"], "presents_turning_point": True, "contradictions": [], "warnings": [],
        "damage_claims": [{"verdict": "correct", "claim": "a"}, {"verdict": "incorrect", "claim": "b"},
                          {"verdict": "unverifiable", "claim": "c"}],
        "figures_outside_evidence": [], "validation_rejections": [], "fallback_notice": False,
        "judge": {"grounded": 4, "actionable": 5, "specific": 3, "consistent": 5, "honest": 4, "issues": ["i"]}}
    return {**row, **override}


def test_summary_counts_the_deterministic_columns_and_means_the_judge() -> None:
    rows = [_row(), _row(id="b", contradictions=["x"], seconds=20.0), {"id": "c", "mode": "bo1", "error": "boom"}]
    summary = bench.summarize(rows)
    assert (summary["answers"], summary["errors"], summary["with_contradiction"]) == (3, 1, 1)
    assert summary["seconds_mean"] == 15.0 and summary["damage_claims_correct"] == "2/4"
    assert summary["judge_mean"]["actionable"] == 5 and summary["judge_mean"]["specific"] == 3


def test_report_keeps_every_answer_verbatim_for_later_comparison() -> None:
    report = {"provider": "openai", "model": "m", "date": "2026-11-24", "commit": "abc", "orchestrator": "adk",
              "grader": "g", "rows": [_row(), {"id": "bo3-e", "mode": "bo3", "error": "boom"}]}
    text = bench.render(report)
    assert "## BO1 summary" in text and "### bo1-x-turning" in text and "> line one" in text
    assert "Error: boom" in text and "ERROR boom" in text


def test_anonymized_row_drops_the_players_and_their_names() -> None:
    from scripts.faithfulness_benchmark.run_log_grounded import anonymize

    row = _row(players=["RealName", "Other"], answer="RealName beat Other")
    clean = bench.anonymized(row, anonymize)
    assert clean["answer"] == "Player A beat Player B" and "players" not in clean


def test_numbered_bold_sections_count_as_headings() -> None:
    answer = "1. **Result**: A won.\n\n2. **Turning point**: turn 14.\n3. **Decisions**: x\n4. **What to change**: y"
    assert len(bench.sections_present(answer)) == 4


def test_simulated_games_become_separate_bo1_cases(tmp_path: Any) -> None:
    import json

    game = {"players": ["SimAlpha", "SimBeta"], "log": "|t:|1", "formatid": "gen9championsvgc2026regmcbo3"}
    for name in ("a", "b"):
        (tmp_path / f"bo3-public-{name}.json").write_text(json.dumps({"games": [game, game]}), encoding="utf-8")
    cases = bench.build_cases(sim_dir=tmp_path, sim_games=3)
    sim = [c for c in cases if c.id.startswith("bo1sim-")]
    assert len(sim) == 3 and all(c.mode == "bo1" and len(c.games) == 1 for c in sim)
    assert {bench.group(c.id) for c in sim} == {"bo1-sim"} and bench.group("bo3-1-x") == "bo3" and bench.group("bo1-x") == "bo1"
    assert len({c.id for c in sim}) == 3
    assert not [c for c in bench.build_cases() if c.id.startswith("bo1sim-")]


def test_only_public_replays_get_a_showdown_link() -> None:
    assert bench.public_url("gen9championsvgc2026regmcbo3-2698400388") == (
        "https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2698400388")
    assert bench.public_url("gen9championsvgc2026regmcbo3-sim700x0g1") == "" and bench.public_url("") == ""


def test_fresh_cases_skip_recorded_forfeited_and_short_games(tmp_path) -> None:
    long_log = "|turn|1|turn|6|win|A"
    logs = {"x-1": long_log, "x-2": long_log + "|-message|B forfeited.", "x-3": "|turn|2|win|A", "x-4": long_log,
            next(iter(bench.REPLAY_DIR.glob("*.json"))).stem: long_log}
    for name, log in logs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps({"log": log}), encoding="utf-8")
    cases = bench.fresh_cases(5, tmp_path)
    assert [c.id for c in cases] == ["bo1-fresh-1-turning", "bo1-fresh-4-coach_loser"]


def test_games_are_anonymized_before_analysis_without_touching_move_names() -> None:
    game = {"players": ["Ash", "x.y"], "log": "|player|p1|Ash|1|\n|move|p1a: Rotom|Flash Cannon|\n|c|☆x.y|gg\n|win|Ash"}
    (out,) = bench.anonymous_games([game])
    assert out["players"] == ["Player A", "Player B"]
    assert "Flash Cannon" in out["log"] and "Ash" not in out["log"].replace("Flash", "")
    assert "|c|" not in out["log"] and out["log"].endswith("|win|Player A")


def test_a_name_right_after_a_line_break_is_anonymized() -> None:
    game = {"players": ["chaunce307", "Bo"], "log": "|raw|x\nchaunce307 has agreed to open team sheets.\nBo joined"}
    (out,) = bench.anonymous_games([game])
    assert "chaunce307" not in out["log"] and out["log"].endswith("Player B joined")



def test_player_ids_and_spectators_never_reach_the_model() -> None:
    log = "\n".join(["|j|☆spectator99", "|player|p1|Mega Phox|1|", "|turn|1", "|c|☆spectator99|hi", "|win|Mega Phox",
                       "|n|☆Player A|megaphox", "|l|☆spectator99", "|raw|megaphox's rating: 1500"])
    (out,) = bench.anonymous_games([{"players": ["Mega Phox", "Bo"], "log": log}])
    assert "spectator99" not in out["log"] and "megaphox" not in out["log"].casefold()
    assert "|player|p1|Player A|1|" in out["log"] and "|raw|Player A's rating: 1500" in out["log"]
