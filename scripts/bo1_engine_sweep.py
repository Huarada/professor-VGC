"""Model-free sweep of the BO1 evidence engine over many simulated games.

    python -m scripts.bo1_engine_sweep --dir .cache/sim-real-teams-v2 --limit 60 --out docs/validation/bo1-sweep

The games come from ``tools/bo3_sim`` (the Showdown simulator on real Reg M-C team sheets, ADR-077), so the log is
the full truth and any number of replays can be generated. Move choice is random there: use this for engine
correctness at scale, not for judging coaching advice. For each game the real BO1 evidence stack runs (real
calculator, Chaos tiers from ``data/chaos-cache``, a scripted LLM, no API call) and these are checked:

- the stack finishes (no exception) and returns one per-move check for every move event of the log;
- the turning point exists exactly when the game has a winner and no forfeit, names the log's winner, holds a lead of
  at least half a Pokemon, and lies inside the game;
- the narration validators against statements built from the log itself: a true "X fainted on turn N" must never be
  flagged (false positives) and the same sentence moved to a turn where X did not faint must always be flagged.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_games(directory: Path, limit: int) -> list[tuple[str, dict[str, Any]]]:
    """Every game of every simulated series, in a stable order, spread over the series when limited."""
    games = [(f"{path.stem.removeprefix('bo3-public-')}-g{index + 1}", game)
             for path in sorted(directory.glob("bo3-public-*.json"))
             for index, game in enumerate(json.loads(path.read_text(encoding="utf-8"))["games"])]
    if limit and limit < len(games):
        step = len(games) / limit
        games = [games[int(i * step)] for i in range(limit)]
    return games


def truth_probes(state: Any) -> tuple[list[str], list[str]]:
    """(true statements, false statements) about when Pokemon fainted, built from the log's own faint list."""
    outcome = state.outcome
    names = {side.player: side.player_name or side.player for side in state.sides}
    faint_turns: dict[tuple[str, str], set[int]] = {}
    for ko in outcome.kos:
        faint_turns.setdefault((ko.player, ko.fainted), set()).add(ko.turn)
    true: list[str] = []
    false: list[str] = []
    for ko in outcome.kos:
        owner = names[ko.player]
        true.append(f"On turn {ko.turn}, {owner}'s {ko.fainted} fainted.")
        true.append(f"On turn {ko.turn} a KO of {owner}'s {ko.fainted} decided the exchange.")
        wrong = ko.turn + 1
        if wrong <= outcome.turns and wrong not in faint_turns[(ko.player, ko.fainted)]:
            false.append(f"On turn {wrong}, {owner}'s {ko.fainted} fainted.")
    return true, false


def check_game(game_id: str, game: dict[str, Any], service: Any, parser: Any) -> dict[str, Any]:
    from src.domain.models import AnalysisRequest
    from src.services.faint_timing import faint_timing_issues
    from src.services.ground_truth import parse_replay

    row: dict[str, Any] = {"id": game_id, "problems": []}
    request = AnalysisRequest(session_id=game_id, replay_json=game, question="What decided the game?")
    start = time.monotonic()
    try:
        result = service.analyze(request)
    except Exception as exc:  # noqa: BLE001 - the sweep reports every failing game
        row["problems"].append(f"exception {type(exc).__name__}: {str(exc)[:160]}")
        return row
    row["seconds"] = round(time.monotonic() - start, 1)
    state = parse_replay(parser, request)
    outcome = state.outcome
    if outcome is None:
        row["problems"].append("no outcome parsed")
        return row
    moves = sum(1 for event in outcome.events if event.kind == "move")
    row["moves"], row["turn_checks"] = moves, len(result.turn_checks)
    if len(result.turn_checks) != moves:
        row["problems"].append(f"{len(result.turn_checks)} turn checks for {moves} move events")
    point = result.turning_point
    expected = bool(outcome.winner_player) and not outcome.forfeited_player and bool(outcome.events)
    if (point is not None) != expected:
        row["problems"].append(f"turning point {'present' if point else 'missing'} but expected {expected}")
    if point is not None:
        if point.winner_player != outcome.winner_player:
            row["problems"].append("turning point names the wrong winner")
        if not 1 <= point.turn <= outcome.turns:
            row["problems"].append(f"turning point turn {point.turn} outside 1-{outcome.turns}")
        if point.rule == "lead_held_to_the_end" and point.candidates[0].lead < 0.5:
            row["problems"].append("held lead below half a Pokemon")
        row["turning_rule"] = point.rule
    true, false = truth_probes(state)
    row["true_probes"], row["false_probes"] = len(true), len(false)
    row["false_positives"] = sum(bool(faint_timing_issues(text, state)) for text in true)
    row["missed_lies"] = sum(not faint_timing_issues(text, state) for text in false)
    if row["false_positives"] or row["missed_lies"]:
        row["problems"].append(f"validator: {row['false_positives']} false positives, {row['missed_lies']} missed")
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if "seconds" in r]
    true, false = sum(r.get("true_probes", 0) for r in done), sum(r.get("false_probes", 0) for r in done)
    return {
        "games": len(rows), "finished": len(done), "with_problems": sum(bool(r["problems"]) for r in rows),
        "seconds_mean": round(statistics.mean(r["seconds"] for r in done), 1) if done else None,
        "turning_rules": dict(Counter(r["turning_rule"] for r in done if "turning_rule" in r)),
        "validator_true_probes": true, "validator_false_positives": sum(r.get("false_positives", 0) for r in done),
        "validator_false_probes": false, "validator_missed_lies": sum(r.get("missed_lies", 0) for r in done),
    }


def render(summary: dict[str, Any], rows: list[dict[str, Any]], directory: str) -> str:
    out = ["# BO1 engine sweep over simulated games", "", f"- date: {date.today().isoformat()}", f"- source: {directory}",
           "- the simulator picks moves at random: this checks the engine, not the quality of advice", "",
           "| games | finished | with problems | mean s | turning rules |", "|---|---|---|---|---|",
           f"| {summary['games']} | {summary['finished']} | {summary['with_problems']} | {summary['seconds_mean']} | "
           f"{summary['turning_rules']} |", "",
           f"Faint-timing validator: {summary['validator_false_positives']} false positives in "
           f"{summary['validator_true_probes']} true statements; {summary['validator_missed_lies']} of "
           f"{summary['validator_false_probes']} wrong-turn statements not flagged.", ""]
    bad = [r for r in rows if r["problems"]]
    out += ["## Games with problems", ""] + ([f"- {r['id']}: {'; '.join(r['problems'])}" for r in bad] or ["none"])
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / ".cache" / "sim-real-teams-v2")
    parser.add_argument("--limit", type=int, default=60, help="games to check, spread over the series (0 = all)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "bo1-sweep")
    parser.add_argument("--chaos", default=str(ROOT / "data" / "chaos-cache"))
    args = parser.parse_args()

    from scripts.run_local import local_environment
    os.environ.update(local_environment(ROOT, os.environ))
    from scripts.faithfulness_benchmark.bench_container import build_container
    from src.adapters.memory.conversation_memory import InMemoryConversationMemory
    from src.adapters.parsers.showdown_parser import ShowdownReplayParser
    from src.services.analysis_service import AnalysisService
    from src.services.selection_service import LLMSelectionService
    from tests.conftest import FakeLLM

    container = build_container(args.chaos)
    llm = FakeLLM(json.dumps({"focus_species": [], "matchups": [], "rationale": "sweep"}), "The game was decided on the board.")
    prompts = container.prompts()
    service = AnalysisService(parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=prompts),
                              evidence=container.ground_truth("openai"), llm=llm, memory=InMemoryConversationMemory(),
                              prompts=prompts)
    games = load_games(args.dir, args.limit)
    rows: list[dict[str, Any]] = []
    try:
        for index, (game_id, game) in enumerate(games, 1):
            row = check_game(game_id, game, service, ShowdownReplayParser())
            rows.append(row)
            print(f"[{index}/{len(games)}] {game_id} {row.get('seconds', '-')}s {row['problems'] or 'ok'}", flush=True)
    finally:
        container.shutdown()
    summary = summarize(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat()
    (args.out / f"{stem}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    (args.out / f"{stem}.md").write_text(render(summary, rows, str(args.dir.relative_to(ROOT)) if args.dir.is_relative_to(ROOT) else str(args.dir)), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
