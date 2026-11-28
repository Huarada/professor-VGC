"""Pass/fail checkup of the six BO1 coaching fixes (ADR-083 to ADR-087) over many games, without a human reader.

Each fix is checked twice where it can be:
  evidence - the engine's own output obeys the fix (no LLM; every game);
  answer   - the final answer the user would read does not show the error (OpenAI answers; --answers).

    python -m scripts.coaching_checkup --dir data/replays/cache/regmc --count 100 --answers 40

The answer checks for items 2-4 are the same validators the pipeline retries on, so a failure means the error reached
the user after the one correction retry; they cannot see strategic mistakes. Items 1 and 5 use text heuristics.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ITEMS = {
    "1_allies": "Allies: no partner counted as a lost action or a Protect read; partner effects listed as synergies",
    "2_switches": "Switches: only to a Pokemon that keeps more than half its HP; advice uses only listed switches",
    "3_alternatives": "Alternatives: no alternative or threat aimed at a Pokemon behind Protect that turn",
    "4_validators": "Validators: moves, targets, faints, KO claims and damage wording match the log and the engine",
    "5_coaching": "Coaching: the timeline names the loser; loser-coaching answers do not coach the winner",
    "6_names": "Names: no real player name reaches the model or the answer",
    "7_turning": "Turning point: the section appears exactly when the question asks for it (ADR-087)",
}
_PROTECT_MOVES = r"Protect|Detect|Spiky Shield|Baneful Bunker|King's Shield|Silk Trap|Burning Bulwark|Obstruct"
_SINGLETURN = re.compile(rf"^\|-singleturn\|(p[12])[a-d]: ([^|]+)\|(?:move: )?(?:{_PROTECT_MOVES})\s*$")
_SWITCH = re.compile(r"^\|(?:switch|drag|replace)\|(p[12])[a-d]: ([^|]+)\|([^,|]+)")
_ANSWER_ONLY = ("4_validators", "7_turning")  # claims in prose; the evidence has nothing to check
_WASTE = r"(?:wasted|misplay|mistake|ineffective|pointless|no effect|did nothing|immune|unaffected)"
_PARTNER = re.compile(r"\b(?:partner|ally|allied|teammate|own)\b", re.I)


def protected_by_turn(log: str) -> dict[int, set[tuple[str, str]]]:
    """(player, species) that a Protect-like move shielded on each turn, read from the raw log."""
    species: dict[tuple[str, str], str] = {}
    protected: dict[int, set[tuple[str, str]]] = {}
    turn = 0
    for line in log.splitlines():
        if line.startswith("|turn|"):
            turn = int(line.split("|")[2])
        elif match := _SWITCH.match(line):
            species[(match[1], match[2])] = match[3]
        elif match := _SINGLETURN.match(line):
            protected.setdefault(turn, set()).add((match[1], species.get((match[1], match[2]), match[2])))
    return protected


def _same(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def evidence_failures(game: dict[str, Any], state: Any, evidence: Any, real_names: list[str]) -> dict[str, list[str]]:
    """Ways the engine's evidence breaks one of the fixes (empty lists = pass)."""
    from src.services.wasted_actions import find_ally_synergies, find_wasted_actions

    fails: dict[str, list[str]] = {key: [] for key in ITEMS if key not in _ANSWER_ONLY}
    teams = {side.player: {mon.species for mon in side.team} for side in state.sides}
    for wasted in find_wasted_actions(state):
        if wasted.target_player == wasted.actor_player:
            fails["1_allies"].append(f"T{wasted.turn} {wasted.actor} {wasted.move}: lost action against its own partner")
    for read in evidence.protect_reads:
        if read.blocker_player == read.attacker_player:
            fails["1_allies"].append(f"T{read.turn} {read.attacker} {read.move}: Protect read of its own partner")
    for synergy in find_ally_synergies(state):
        if not any(_same(synergy.ally, mon) for mon in teams.get(synergy.actor_player, set())):
            fails["1_allies"].append(f"T{synergy.turn} {synergy.move}: synergy with {synergy.ally}, not on the actor's team")
    shielded = protected_by_turn(str(game.get("log", "")))
    for check in evidence.turn_checks:
        guarded = shielded.get(check.turn, set())
        for option in check.decision_options:
            if option.switch_to and option.max_percent_taken is not None and option.max_percent_taken >= 50:
                fails["2_switches"].append(f"T{check.turn} switch to {option.switch_to}: takes {option.max_percent_taken}%")
        for alt in check.best_alternatives:
            if any(p == alt.target_player and _same(alt.target, s) for p, s in guarded):
                fails["3_alternatives"].append(f"T{check.turn} {check.actor}: alternative into {alt.target} behind Protect")
        for threat in check.incoming_threats:
            if any(p == threat.attacker_player and _same(threat.attacker, s) for p, s in guarded):
                fails["3_alternatives"].append(f"T{check.turn} {check.actor}: threat from {threat.attacker} behind Protect")
    outcome = state.outcome
    if outcome is not None and outcome.winner_player:
        loser = next(s for s in state.sides if s.player != outcome.winner_player)
        if f"Loser: {loser.player_name or loser.player}." not in evidence.battle_result:
            fails["5_coaching"].append("the timeline does not name the loser")
    sent = json.dumps(game, ensure_ascii=False).casefold() + evidence.battle_result.casefold()
    fails["6_names"] += ["a real player name reaches the model" for name in real_names if name.casefold() in sent][:1]
    return fails


def _sentences(answer: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+|\n+", answer) if part.strip()]


def answer_failures(answer: str, question_key: str, question: str, state: Any, checks: list[Any],
                    real_names: list[str]) -> dict[str, list[str]]:
    """Errors the final answer still shows, per fix (empty lists = pass)."""
    from scripts.coaching_testbench import sections_present
    from src.services.action_claims import action_claim_issues
    from src.services.advice_claims import ko_claim_issues, presence_issues, switch_advice_issues
    from src.services.damage_wording import remaining_hp_as_damage_issues
    from src.services.faint_timing import faint_timing_issues
    from src.services.side_attribution import side_attribution_issues
    from src.services.turning_point import asks_turning_point
    from src.services.wasted_actions import find_ally_synergies

    fails: dict[str, list[str]] = {key: [] for key in ITEMS}
    fails["1_allies"] += side_attribution_issues(answer, state)
    labels = {side.player: side.player_name or side.player for side in state.sides}
    for synergy in find_ally_synergies(state):
        mirrored = any(_same(synergy.ally, mon.species) for side in state.sides if side.player != synergy.actor_player
                       for mon in side.team)
        for sentence in _sentences(answer):
            # in a mirror an unowned name may be the opponent's copy, which was a real waste
            if mirrored and f"{labels[synergy.actor_player]}'s {synergy.ally}".casefold() not in sentence.casefold():
                continue
            ally = re.escape(synergy.ally)
            # the waste is said OF the partner ("Sceptile was immune", "wasted on Sceptile"), not merely near it
            of_ally = re.search(rf"\b{ally}\b.{{0,25}}\b{_WASTE}\b|\b{_WASTE}\s+(?:on|against|into)\s+(?:\S+\s+)?{ally}\b",
                                sentence, re.I)
            if synergy.move.casefold() in sentence.casefold() and of_ally and not _PARTNER.search(sentence):
                fails["1_allies"].append(f"partner synergy called a waste: {sentence.strip()[:160]}")
    fails["2_switches"] += switch_advice_issues(answer, state, checks)
    fails["3_alternatives"] += presence_issues(answer, state)
    fails["4_validators"] += (ko_claim_issues(answer, state, checks) + action_claim_issues(answer, state)
                              + faint_timing_issues(answer, state) + remaining_hp_as_damage_issues(answer, state))
    outcome = state.outcome
    if question_key in ("coach_loser", "turning") and outcome is not None and outcome.winner_player:
        winner = next(s for s in state.sides if s.player == outcome.winner_player)
        label = re.escape(winner.player_name or winner.player)
        coached = re.compile(rf"\b{label}(?:'s \w+)?\s+(?:should|could have(?! been)|needs? to|must|ought)\b", re.I)
        fails["5_coaching"] += [f"coaches the winner: {s.strip()[:160]}" for s in _sentences(answer) if coached.search(s)]
    fails["6_names"] += ["a real player name is in the answer" for name in real_names if name.casefold() in answer.casefold()][:1]
    has_section = "turning point" in sections_present(answer)
    if has_section != asks_turning_point(question):
        fails["7_turning"].append("turning-point section " + ("without being asked" if has_section else "missing"))
    return fails


def real_player_names(game: dict[str, Any]) -> list[str]:
    names = {str(p) for p in game.get("players", [])}
    names |= {line.split("|")[3] for line in str(game.get("log", "")).splitlines()
              if line.startswith("|player|") and len(line.split("|")) > 3}
    return sorted(name for name in names if len(name.strip()) >= 3)


def tally(rows: list[dict[str, Any]], layer: str) -> dict[str, dict[str, Any]]:
    from scripts.faithfulness_benchmark.stats import wilson_interval

    out: dict[str, dict[str, Any]] = {}
    for key in ITEMS:
        judged = [r for r in rows if layer in r and key in r[layer]]
        if not judged:
            continue
        failed = sum(bool(r[layer][key]) for r in judged)
        low, high = wilson_interval(len(judged) - failed, len(judged))
        out[key] = {"games": len(judged), "failed": failed, "pass_rate": round(1 - failed / len(judged), 3),
                    "pass_95ci": [round(low, 3), round(high, 3)]}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=ROOT / "data" / "replays" / "cache")
    parser.add_argument("--count", type=int, default=100, help="games checked at the evidence layer")
    parser.add_argument("--answers", type=int, default=0, help="of those, how many get an OpenAI answer checked")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "coaching")
    args = parser.parse_args()

    from scripts.run_local import local_environment
    os.environ.update(local_environment(ROOT, os.environ))
    from scripts.local_runtime import configure_trust
    configure_trust(windows=os.name == "nt")
    from scripts import coaching_testbench as bench
    from scripts.faithfulness_benchmark.bench_container import build_container
    from src.adapters.parsers.showdown_parser import ShowdownReplayParser
    from src.domain.models import AnalysisRequest, SelectionPlan
    from src.services.ground_truth import parse_replay

    logging.basicConfig(level=logging.WARNING)
    cases = bench.fresh_cases(args.count, args.dir)
    container = build_container(str(bench.CHAOS_CACHE))
    rows: list[dict[str, Any]] = []
    stem = f"{date.today().isoformat()}-checkup-{args.dir.name}-{len(cases)}"
    try:
        for index, case in enumerate(cases):
            original = case.games[0]
            game = bench.anonymous_games([original])[0]
            names = real_player_names(original)
            question = bench.QUESTIONS[case.question]
            request = AnalysisRequest(session_id=case.id, replay_json=game, question=question, provider=args.provider)
            state = parse_replay(ShowdownReplayParser(), request)
            evidence = container.ground_truth(args.provider).assemble(
                request=request, game_state=state, selection=SelectionPlan(), history=[])
            row: dict[str, Any] = {"id": case.id, "question_key": case.question,
                                   "evidence": evidence_failures(game, state, evidence, names)}
            if index < args.answers:
                result = container.build_pipeline(args.provider, None).analyze(request)
                row["text"] = result.answer
                row["answer"] = answer_failures(result.answer, case.question, question, state, result.turn_checks, names)
            rows.append(row)
            failed = [k for layer in ("evidence", "answer") for k, v in row.get(layer, {}).items() if v]
            print(f"[{index + 1}/{len(cases)}] {case.id} fails={failed}", flush=True)
    finally:
        container.shutdown()
        report = {"date": date.today().isoformat(), "dir": args.dir.name, "items": ITEMS,
                  "evidence": tally(rows, "evidence"), "answer": tally(rows, "answer"), "rows": rows}
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"evidence": report["evidence"], "answer": report["answer"]}, indent=1))


if __name__ == "__main__":
    main()
