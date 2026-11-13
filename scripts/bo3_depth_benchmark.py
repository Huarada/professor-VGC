"""Matched Low vs Medium Gemini depth comparison on the recorded public BO3 (ADR-074).

    python -m scripts.bo3_depth_benchmark --repeats 2 --out docs/bo3-inference/validation/depth-benchmark.json

Every run is a full ``Bo3AnalysisService`` pass (same replays, same question, same orchestrator);
only ``thinking_level`` changes. Runs alternate Low/Medium to spread provider drift. Metrics:

- latency: wall time and the inference / reviews / synthesis split the service already records;
- determinism: a digest of the inverse bounds, which must not depend on the thinking level;
- damage fidelity: every ``damage_range`` claim in the per-game reviews, extracted by one fixed
  judge model, is checked against the damage the raw log shows (the log-grounded benchmark's
  verifier and tolerance). Claims absent from the log count as failures; Low vs Medium is
  compared with an exploratory Fisher exact test because claims within a run are dependent;
- guard events: how often the narrative fell back to the verified findings;
- coverage: whether the series answer addresses the Floette-Eternal and Garchomp the question names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUESTIONS = {
    "spread": (
        "Analyze the original Champions stat-point spreads for both players across this BO3. Use only the public "
        "teamsheets, recorded battle events, and verified damage calculations. For each Pokémon, report the compatible "
        "native stat-point interval for HP, Atk, Def, SpA, SpD, and Spe; distinguish narrowed intervals from unresolved "
        "ones. Explain which observations from which games support each reduction, especially for Huarada's "
        "Floette-Eternal and Garchomp. Separate native points from calculated level-50 stats. Do not guess a complete "
        "spread, call an interval endpoint an exact value, or use common competitive sets as evidence. State clearly "
        "when the replay does not contain enough information."),
    "damage": (
        "Walk me through each game turn by turn. For the important attacks, say how much damage each one dealt as a "
        "percentage of the target's HP, and whether that amount was expected for that attacker and move."),
}
FALLBACK_MARKERS = ("did not pass evidence validation", "allocation", "replaced")


def digest(result: Any) -> str:
    """Stable hash of the deterministic bounds, independent of any generated prose."""
    inference = result.inference
    payload = inference.model_dump(mode="json") if inference is not None else {}
    keep = {key: value for key, value in payload.items() if key in ("checkpoints", "summaries", "outcome")}
    return hashlib.sha256(json.dumps(keep or payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def score_game(llm: Any, game: dict[str, Any], analysis: Any, hits: list[Any], band: Any) -> list[dict[str, Any]]:
    from scripts.faithfulness_benchmark.aggregate import extract_and_filter
    from scripts.faithfulness_benchmark.ground_truth import GroundTruth
    from scripts.faithfulness_benchmark.log_claims import (
        verify_damage_claim_against_log,
    )
    from src.adapters.parsers.showdown_parser import ShowdownReplayParser

    truth = GroundTruth.build(ShowdownReplayParser().parse(game), analysis)
    claims = [c for c in extract_and_filter(llm, analysis.answer, truth, "bo3-review") if c.claim_type == "damage_range"]
    verdicts = [verify_damage_claim_against_log(claim, hits, band) for claim in claims]
    return [{"claim": f"{v.claim.attacker} {v.claim.move} -> {v.claim.defender} "
                      f"{v.claim.min_percent}-{v.claim.max_percent}%", "verdict": v.verdict, "reason": v.reason}
            for v in verdicts]


def one_run(container: Any, games: list[dict[str, Any]], question: str, level: str, tag: str, judge: Any,
            hits: list[list[Any]], band: Any, provider: str) -> dict[str, Any]:
    from src.domain.series import Bo3AnalysisRequest

    request = Bo3AnalysisRequest(session_id=f"depth-{tag}", games=list(games), question=question, provider=provider)
    start = time.monotonic()
    result = container.build_bo3_pipeline(provider, thinking_level=level).analyze(request)
    wall = time.monotonic() - start
    claims = [item for game, analysis, observed in zip(games, result.games, hits)
              for item in score_game(judge, game, analysis, observed, band)]
    answer = result.answer
    lowered = answer.lower()
    return {
        "level": level, "tag": tag, "wall_seconds": round(wall, 1),
        "inference_seconds": result.inference_seconds, "reviews_wall_seconds": result.reviews_wall_seconds,
        "synthesis_seconds": result.synthesis_seconds,
        "review_model_seconds": [round(t.model_seconds, 1) for t in result.review_timings],
        "inference_digest": digest(result),
        "fallback_notice": any(marker in lowered for marker in FALLBACK_MARKERS[:1]),
        "mentions_floette": "floette" in lowered, "mentions_garchomp": "garchomp" in lowered,
        "answer_chars": len(answer), "game_answer_chars": [len(g.answer) for g in result.games],
        "damage_claims": claims, "answer": answer,
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts.faithfulness_benchmark.stats import fisher_exact_2x2, format_rate

    out: dict[str, Any] = {}
    for level in ("low", "medium"):
        mine = [r for r in runs if r["level"] == level]
        claims = [c for r in mine for c in r["damage_claims"]]
        good = sum(c["verdict"] == "correct" for c in claims)
        bad = sum(c["verdict"] in ("incorrect", "not_in_log") for c in claims)
        walls = [r["wall_seconds"] for r in mine]
        out[level] = {
            "runs": len(mine), "wall_seconds_mean": round(statistics.mean(walls), 1) if walls else None,
            "wall_seconds_range": [min(walls), max(walls)] if walls else None,
            "reviews_wall_mean": round(statistics.mean(r["reviews_wall_seconds"] or 0 for r in mine), 1) if mine else None,
            "synthesis_mean": round(statistics.mean(r["synthesis_seconds"] or 0 for r in mine), 1) if mine else None,
            "damage_claims": {"correct": good, "incorrect": bad,
                              "not_in_log": sum(c["verdict"] == "not_in_log" for c in claims),
                              "rate": format_rate(good, good + bad)},
            "fallback_notices": sum(r["fallback_notice"] for r in mine),
            "mentions_both_named": sum(r["mentions_floette"] and r["mentions_garchomp"] for r in mine),
            "digests": sorted({r["inference_digest"] for r in mine}),
        }
    counts = {lv: (out[lv]["damage_claims"]["correct"], out[lv]["damage_claims"]["incorrect"]) for lv in ("low", "medium")}
    if all(sum(v) for v in counts.values()):
        fisher = fisher_exact_2x2(*counts["low"], *counts["medium"])
        out["fisher_low_vs_medium"] = fisher.summary()
    out["deterministic_identical_across_levels"] = len({d for lv in ("low", "medium") for d in out[lv]["digests"]}) == 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--questions", default="spread,damage")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--relative-tolerance", type=float, default=0.05)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "bo3-inference" / "validation" / "depth-benchmark.json")
    args = parser.parse_args()

    from scripts.run_local import local_environment
    os.environ.update(local_environment(ROOT, os.environ))
    from scripts.local_runtime import configure_trust
    configure_trust(windows=os.name == "nt")

    from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
    from scripts.faithfulness_benchmark.run_log_grounded import anonymize
    from scripts.faithfulness_benchmark.tolerance import ToleranceBand
    from src.services.container import Container

    games = json.loads((ROOT / "tests/fixtures/series/bo3-public-2709311277.json").read_text(encoding="utf-8"))["games"]
    hits = [extract_observed_hits(str(game["log"])) for game in games]
    band = ToleranceBand(relative=args.relative_tolerance)
    container = Container()
    judge = container.build_llm(args.provider)  # one fixed judge for both depths
    report: dict[str, Any] = {"provider": args.provider, "repeats": args.repeats, "questions": {}}
    players = [str(p) for game in games for p in game.get("players", [])]
    try:
        for name in args.questions.split(","):
            runs: list[dict[str, Any]] = []
            for repeat in range(args.repeats):
                for level in (("low", "medium") if repeat % 2 == 0 else ("medium", "low")):
                    print(f"[{name}] repeat {repeat + 1} level {level} ...", flush=True)
                    run = one_run(container, games, QUESTIONS[name], level, f"{name}-{level}-{repeat}", judge, hits,
                                  band, args.provider)
                    print(f"   {run['wall_seconds']}s claims={len(run['damage_claims'])} digest={run['inference_digest']}", flush=True)
                    runs.append(run)
                    report["questions"][name] = {"summary": summarize(runs), "runs": runs}
                    text = json.dumps(report, ensure_ascii=False, indent=1)
                    args.out.write_text(anonymize(text, players), encoding="utf-8")
    finally:
        container.shutdown()
    print(json.dumps({k: v["summary"] for k, v in report["questions"].items()}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
