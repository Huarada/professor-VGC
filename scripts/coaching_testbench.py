"""Coaching-quality testbench for BO1 and BO3 answers, driven by the OpenAI API (any provider works).

    python -m scripts.coaching_testbench --provider openai --out docs/validation/coaching

Every case is a full pipeline pass over a recorded replay or series (real calculator, real Chaos tiers from
``data/chaos-cache``, the configured orchestrator). Each answer is scored two ways and stored verbatim in a Markdown
report so two runs (a prompt change, another model) can be compared side by side:

- deterministic checks, no model involved: contradictions with the log (wrong side, a faint on the wrong turn),
  damage claims verified against the damage the raw log shows (the log-grounded benchmark's extractor and verifier),
  percentages that appear nowhere in the evidence, expected structure, turning-point coverage, warnings and fallbacks;
- a rubric judge (one model call, temperature 0): grounded, actionable, specific, consistent, honest about
  uncertainty, 1-5 each. The judge is subjective and a small model is a weak judge: read the deterministic columns first.

Real player names are replaced by "Player A/B" before anything is written to disk.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SERIES_DIR = ROOT / "tests" / "fixtures" / "series"
REPLAY_DIR = ROOT / "tests" / "fixtures" / "replays"
CHAOS_CACHE = ROOT / "data" / "chaos-cache"

QUESTIONS = {
    "turning": "What was the turning point of this game and what should the loser have done differently?",
    "coach_loser": (
        "Coach the losing player: what should they change in their leads, their moves and their use of Protect and "
        "speed control to win this matchup next time? Ground every recommendation in this game."),
    "decisions": (
        "Which decisions by each player were the best and the worst, and why? Include targeting and Protect choices."),
    "coach_series": (
        "Coach both players after this series: what should each change in their leads, bring-four choices and move "
        "choices next time? Ground every recommendation in the games and say what the replays do not show."),
    "spread": (
        "Analyze the original Champions stat-point spreads for both players across this BO3. Use only the public "
        "teamsheets, recorded battle events and verified damage calculations. For each Pokemon report the compatible "
        "native stat-point interval for HP, Atk, Def, SpA, SpD and Spe, and distinguish narrowed intervals from "
        "unresolved ones. State clearly when the replay does not contain enough information."),
}
SECTIONS = ("result", "turning point", "decisions", "what to change")
RUBRIC = ("grounded", "actionable", "specific", "consistent", "honest")


@dataclass(frozen=True)
class Case:
    id: str
    mode: str  # "bo1" | "bo3"
    question: str
    games: list[dict[str, Any]]
    source: str


def _series_games(series_id: str) -> list[dict[str, Any]]:
    path = SERIES_DIR / f"bo3-public-showteam-{series_id}.json"
    if not path.exists():
        path = SERIES_DIR / f"bo3-public-{series_id}.json"
    return list(json.loads(path.read_text(encoding="utf-8"))["games"])


def build_cases(*, quick: bool = False) -> list[Case]:
    cases: list[Case] = []
    recorded = sorted(REPLAY_DIR.glob("*.json"))
    for path in recorded[: 1 if quick else None]:
        replay = json.loads(path.read_text(encoding="utf-8"))
        for key in ("turning", "coach_loser", "decisions"):
            cases.append(Case(f"bo1-{path.stem[-10:]}-{key}", "bo1", key, [replay], f"recorded replay {path.stem}"))
    for series_id in ("2698400388", "2702301708", "2712545359")[: 1 if quick else None]:
        game = _series_games(series_id)[0]
        cases.append(Case(f"bo1-m-c-{series_id}-coach_loser", "bo1", "coach_loser", [game],
                          f"game 1 of public series {series_id} (Reg M-C)"))
    series = [("2709311277", ("coach_series", "spread"))] + [(s, ("coach_series",)) for s in
                                                              ("2698400388", "2702301708", "2712545359")]
    for series_id, keys in series[: 1 if quick else None]:
        games = _series_games(series_id)
        for key in keys:
            cases.append(Case(f"bo3-{series_id}-{key}", "bo3", key, games, f"public series {series_id}"))
    return cases


# ---- deterministic checks (pure, unit-tested) ------------------------------------------------------------------

_PERCENT = re.compile(r"\d+(?:\.\d+)?%")
_ADVICE = re.compile(r"\b(?:should|consider|try|avoid|instead|could have|ought|needs? to|recommend)\b", re.I)


_HEADING = re.compile(r"\s*(?:#|\*\*|\d+[.)]\s*\*{0,2})")


def sections_present(answer: str) -> list[str]:
    heads = [re.sub(r"[^a-z ]", "", line).strip() for line in answer.lower().splitlines() if _HEADING.match(line)]
    return [name for name in SECTIONS if any(name in head for head in heads)]


def figures_outside_evidence(answer: str, evidence_text: str) -> list[str]:
    return sorted({p for p in _PERCENT.findall(answer) if p not in evidence_text})


def advice_sentences(answer: str) -> int:
    return sum(bool(_ADVICE.search(part)) for part in re.split(r"(?<=[.!?])\s+|\n+", answer))


def mentions_turn(answer: str, turn: int | None) -> bool | None:
    if turn is None:
        return None
    return bool(re.search(rf"(?<![\w-])turns?\s+(?:\d+\s*(?:,|and|&|-|to)\s*)*{turn}(?![\w-])", answer, re.I))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if "error" not in r]
    claims = [c for r in done for c in r["damage_claims"]]
    correct = sum(c["verdict"] == "correct" for c in claims)
    judged = sum(c["verdict"] in ("correct", "incorrect", "not_in_log") for c in claims)
    scores = [r["judge"] for r in done if r.get("judge")]
    return {
        "answers": len(rows), "errors": len(rows) - len(done),
        "seconds_mean": round(statistics.mean(r["seconds"] for r in done), 1) if done else None,
        "with_contradiction": sum(bool(r["contradictions"]) for r in done),
        "damage_claims_correct": f"{correct}/{judged}",
        "with_unsupported_figures": sum(bool(r["figures_outside_evidence"]) for r in done),
        "warnings": sum(len(r["warnings"]) for r in done),
        "judge_mean": {k: round(statistics.mean(s[k] for s in scores), 2) for k in RUBRIC} if scores else None,
    }


# ---- model-backed pieces ---------------------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You grade one Pokemon VGC coaching answer against the battle evidence it was given. Score each criterion from 1 "
    "(poor) to 5 (excellent), strictly: grounded = every claim traceable to the evidence, nothing invented; actionable "
    "= concrete changes the player can apply next time; specific = names turns, moves and Pokemon; consistent = no "
    "contradiction with the log (sides, turns, faints, who moved first); honest = states what the evidence cannot "
    "show. Return only JSON: {\"grounded\":n,\"actionable\":n,\"specific\":n,\"consistent\":n,\"honest\":n,"
    "\"issues\":[up to three short strings]}.")


def judge(llm: Any, question: str, answer: str, evidence: str) -> dict[str, Any] | None:
    from src.domain.models import ChatMessage

    body = f"QUESTION:\n{question}\n\nEVIDENCE (battle log summary):\n{evidence[:30000]}\n\nANSWER TO GRADE:\n{answer}"
    try:
        raw = llm.complete(system=JUDGE_SYSTEM, messages=[ChatMessage(role="user", content=body)],
                           temperature=0.0, json_mode=True)
        data = json.loads(raw)
        return {**{k: int(data[k]) for k in RUBRIC}, "issues": [str(i) for i in data.get("issues", [])][:3]}
    except Exception as exc:  # noqa: BLE001 - a failed judge call must not hide the answer
        print(f"   judge failed: {type(exc).__name__}", flush=True)
        return None


class _Rejections(logging.Handler):
    """Collects the BO3 narrative-validation rejections the pipeline logs while a case runs."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.reasons: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if "narrative validation attempt" in text:
            self.reasons.append(text.split("rejected: ", 1)[-1][:160])


def run_case(case: Case, container: Any, judge_llm: Any, claim_llm: Any, args: argparse.Namespace) -> dict[str, Any]:
    from scripts.bo3_depth_benchmark import score_game
    from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
    from scripts.faithfulness_benchmark.tolerance import ToleranceBand
    from src.adapters.parsers.showdown_parser import ShowdownReplayParser
    from src.domain.models import AnalysisRequest
    from src.domain.series import Bo3AnalysisRequest
    from src.services.side_attribution import narration_issues

    question = QUESTIONS[case.question]
    rejections = _Rejections()
    logging.getLogger().addHandler(rejections)
    start = time.monotonic()
    if case.mode == "bo1":
        result: Any = container.build_pipeline(args.provider, args.orchestrator).analyze(
            AnalysisRequest(session_id=case.id, replay_json=case.games[0], question=question, provider=args.provider))
        answer, per_game = result.answer, [result]
        warnings = list(result.regulation_warnings)
        turning = result.turning_point.turn if result.turning_point is not None else None
        evidence = result.battle_result
    else:
        result = container.build_bo3_pipeline(args.provider, args.orchestrator, thinking_level="low").analyze(
            Bo3AnalysisRequest(session_id=case.id, games=list(case.games), question=question, provider=args.provider))
        answer, per_game = result.answer, list(result.games)
        warnings = [w for g in per_game for w in g.regulation_warnings]
        turning = None
        evidence = "\n\n".join(f"GAME {i + 1}\n{g.battle_result}" for i, g in enumerate(per_game))
    seconds = round(time.monotonic() - start, 1)
    logging.getLogger().removeHandler(rejections)

    parser = ShowdownReplayParser()
    band = ToleranceBand(relative=0.05)
    contradictions: list[str] = []
    claims: list[dict[str, Any]] = []
    for game, analysis in zip(case.games, per_game):
        contradictions += narration_issues(analysis.answer, parser.parse(game))
        claims += score_game(claim_llm, game, analysis, extract_observed_hits(str(game["log"])), band)
    evidence_text = result.model_dump_json()
    texts = [answer] + [g.answer for g in per_game]
    return {
        "players": [str(p) for p in case.games[0].get("players", [])],
        "id": case.id, "mode": case.mode, "question_key": case.question, "question": question, "source": case.source,
        "seconds": seconds, "chars": len(answer), "answer": answer,
        "game_answers": [g.answer for g in per_game] if case.mode == "bo3" else [],
        "sections": sections_present(answer) if case.mode == "bo1" else None,
        "turning_point_turn": turning, "mentions_turning_point": mentions_turn(answer, turning),
        "advice_sentences": advice_sentences(answer),
        "contradictions": contradictions, "damage_claims": claims, "warnings": warnings,
        "figures_outside_evidence": figures_outside_evidence(" ".join(texts), evidence_text),
        "validation_rejections": rejections.reasons,
        "fallback_notice": "did not pass evidence validation" in answer.lower(),
        "judge": judge(judge_llm, question, answer, evidence),
    }


# ---- report ----------------------------------------------------------------------------------------------------

def render(report: dict[str, Any]) -> str:
    out = [f"# Coaching testbench: {report['provider']} / {report['model']}", "",
           f"- date: {report['date']}", f"- commit: {report['commit']}", f"- orchestrator: {report['orchestrator']}", f"- grader (judge and claim extractor): {report['grader']}",
           f"- cases: {len(report['rows'])}", "",
           "Deterministic columns come first; the judge scores are subjective (one grader-model call per answer).", ""]
    for mode in ("bo1", "bo3"):
        rows = [r for r in report["rows"] if r["mode"] == mode]
        if not rows:
            continue
        s = summarize(rows)
        out += [f"## {mode.upper()} summary", "",
                "| answers | errors | mean s | contradictions | damage claims correct | unsupported figures | warnings |",
                "|---|---|---|---|---|---|---|",
                f"| {s['answers']} | {s['errors']} | {s['seconds_mean']} | {s['with_contradiction']} answers | "
                f"{s['damage_claims_correct']} | {s['with_unsupported_figures']} answers | {s['warnings']} |", ""]
        if s["judge_mean"]:
            out += ["Judge mean (1-5): " + ", ".join(f"{k} {v}" for k, v in s["judge_mean"].items()), ""]
        out += ["| case | s | sections | turning point | contradictions | claims | figures outside evidence | rejections | judge |",
                "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if "error" in r:
                out.append(f"| {r['id']} | | | | | | | | ERROR {r['error'][:80]} |")
                continue
            claims = r["damage_claims"]
            good = sum(c["verdict"] == "correct" for c in claims)
            j = r["judge"]
            scores = " ".join(str(j[k]) for k in RUBRIC) if j else "-"
            sections = "-" if r["sections"] is None else f"{len(r['sections'])}/4"
            turning = "-" if r["mentions_turning_point"] is None else str(r["mentions_turning_point"])
            out.append(f"| {r['id']} | {r['seconds']} | {sections} | {turning} | {len(r['contradictions'])} | "
                       f"{good}/{len(claims)} | {len(r['figures_outside_evidence'])} | {len(r['validation_rejections'])} | {scores} |")
        out.append("")
    out += ["## Answers", ""]
    for r in report["rows"]:
        out += [f"### {r['id']}", ""]
        if "error" in r:
            out += [f"Error: {r['error']}", ""]
            continue
        out += [f"Source: {r['source']}. Question: {r['question']}", "",
                f"Checks: contradictions {r['contradictions'] or 'none'}; warnings "
                f"{[w for w in r['warnings'] if 'No usage data' not in w] or 'none'}; narrative-validation rejections "
                f"{len(r['validation_rejections'])}; figures outside evidence "
                f"{r['figures_outside_evidence'] or 'none'}; fallback notice {r['fallback_notice']}.", ""]
        if r["judge"]:
            scores = ", ".join(k + " " + str(r["judge"][k]) for k in RUBRIC)
            out += [f"Judge: {scores}. Issues: {r['judge']['issues'] or 'none'}", ""]
        out += [*(f"> {line}" if line else ">" for line in r["answer"].splitlines()), ""]
    return "\n".join(out)


def anonymized(row: dict[str, Any], anonymize: Any) -> dict[str, Any]:
    """The row with its own players' real names replaced (each case names its players Player A/B)."""
    data: dict[str, Any] = json.loads(anonymize(json.dumps(row, ensure_ascii=False), row.get("players", [])))
    data.pop("players", None)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--orchestrator", default=None, help="adk | langchain | native (default: configured)")
    parser.add_argument("--grader-model", default=None,
                        help="OpenAI model for the rubric judge and the claim extractor (default: the run model)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "coaching")
    parser.add_argument("--chaos", default=str(CHAOS_CACHE))
    parser.add_argument("--quick", action="store_true", help="one case per kind, for a smoke run")
    parser.add_argument("--only", default="", help="substring filter on case ids")
    args = parser.parse_args()

    from scripts.run_local import local_environment
    os.environ.update(local_environment(ROOT, os.environ))
    from scripts.local_runtime import configure_trust
    configure_trust(windows=os.name == "nt")
    from scripts.faithfulness_benchmark.bench_container import build_container
    from scripts.faithfulness_benchmark.run_log_grounded import anonymize

    container = build_container(args.chaos)
    settings = container.settings
    model = getattr(settings, f"{args.provider}_model", args.provider)
    claim_llm = container.build_llm(args.provider)
    if args.grader_model and args.provider == "openai":
        from src.adapters.llm.openai_provider import OpenAIProvider
        claim_llm = OpenAIProvider(settings.openai_api_key, args.grader_model)
    judge_llm = claim_llm
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    report: dict[str, Any] = {"provider": args.provider, "model": model, "orchestrator": args.orchestrator or settings.orchestrator,
                              "grader": args.grader_model or model,
                              "date": date.today().isoformat(), "commit": commit, "rows": []}
    cases = [c for c in build_cases(quick=args.quick) if args.only in c.id]
    stem = f"{report['date']}-{args.provider}-{model}".replace("/", "_") + ("-quick" if args.quick else "")
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        for index, case in enumerate(cases, 1):
            print(f"[{index}/{len(cases)}] {case.id} ...", flush=True)
            try:
                row = run_case(case, container, judge_llm, claim_llm, args)
                print(f"   {row['seconds']}s contradictions={len(row['contradictions'])} judge={row['judge']}", flush=True)
            except Exception as exc:  # noqa: BLE001 - one failing case must not end the run
                row = {"id": case.id, "mode": case.mode, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                print(f"   ERROR {row['error']}", flush=True)
            report["rows"].append(row)
            safe = {**report, "rows": [anonymized(r, anonymize) for r in report["rows"]]}
            (args.out / f"{stem}.json").write_text(json.dumps(safe, ensure_ascii=False, indent=1), encoding="utf-8")
            (args.out / f"{stem}.md").write_text(render(safe), encoding="utf-8")
    finally:
        container.shutdown()
    print(f"wrote {args.out / (stem + '.md')}")


if __name__ == "__main__":
    main()
