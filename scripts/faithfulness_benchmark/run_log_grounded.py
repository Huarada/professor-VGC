"""Log-grounded benchmark: score the AI's damage claims against REAL games.

    python -m scripts.faithfulness_benchmark.run_log_grounded \
        [--replays data/replays/cache] [--limit 10] [--provider openai] \
        [--orchestrator native] [--chaos firestore|local] [--out FILE.json]

Fixes the circularity of ``run.py``, whose ``damage_range`` verdicts compare a
claim with the pipeline's own projection (the numbers Condition A was handed).
Here, for each real public replay:

1. Condition A (the real pipeline) and Condition B (same LLM, raw log only)
   answer the same question;
2. the same judge extracts atomic claims from both answers;
3. every ``damage_range`` claim is checked against the damage the LOG shows
   (``observed_damage.py``, an independent reader of the raw log) — one
   external truth for both conditions (``log_claims.py``);
4. Condition A's claims are ALSO checked the old way, and the two verdicts
   are cross-tabulated (``circularity_matrix``) to show how often "matches
   the projection" differed from "matches what happened".

The deterministic layer itself is calibrated against the same logs in
``run_engine_calibration.py`` (no LLM).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from scripts.faithfulness_benchmark.aggregate import extract_and_filter
from scripts.faithfulness_benchmark.bench_container import build_container
from scripts.faithfulness_benchmark.engine_calibration import calibrate
from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.log_claims import (
    LogClaimVerdict,
    circularity_matrix,
    verify_damage_claim_against_log,
)
from scripts.faithfulness_benchmark.naive_baseline import run_naive_baseline
from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
from scripts.faithfulness_benchmark.replay_corpus import DEFAULT_CACHE_DIR, load_replays
from scripts.faithfulness_benchmark.run import _with_retry
from scripts.faithfulness_benchmark.stats import fisher_exact_2x2
from scripts.faithfulness_benchmark.verify import verify_claim
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest

QUESTION = (
    "Walk me through this battle turn by turn. For the important attacks, say how "
    "much damage each one dealt as a percentage of the target's HP, and whether "
    "that amount was expected for that attacker and move."
)
_OUT_DIR = Path(__file__).resolve().parent / "out"


def anonymize(text: str, players: list[str]) -> str:
    """Replace the real player names of a public replay with "Player A/B"
    before anything is written to disk."""
    for index, name in enumerate(players):
        if name:
            text = text.replace(name, f"Player {'AB'[index] if index < 2 else index + 1}")
    return text


def _tally(verdicts: list[LogClaimVerdict]) -> dict[str, int]:
    counts = Counter(v.verdict for v in verdicts)
    return {k: counts.get(k, 0) for k in ("correct", "incorrect", "not_in_log", "unverifiable")}


def _run_replay(
    container: Any, replay_id: str, replay: dict[str, Any], provider: str, orchestrator: str
) -> dict[str, Any]:
    print(f"=== {replay_id} ===")
    hits = extract_observed_hits(str(replay["log"]))
    game_state = ShowdownReplayParser().parse(replay)
    pipeline = container.build_pipeline(provider=provider, orchestrator=orchestrator)
    analysis = _with_retry(
        lambda: pipeline.analyze(
            AnalysisRequest(
                session_id=f"logbench-{replay_id}", replay_json=replay,
                question=QUESTION, provider=provider,
            )
        ),
        label=f"{replay_id}: condition A",
    )
    gt = GroundTruth.build(game_state, analysis)
    llm = container.build_llm(provider)
    naive = _with_retry(
        lambda: run_naive_baseline(llm, str(replay["log"]), QUESTION),
        label=f"{replay_id}: condition B",
    )

    result: dict[str, Any] = {
        "replay_id": replay_id,
        "observed_hits": len(hits),
        "engine_calibration": calibrate(hits, analysis.turn_checks).summary(),
    }
    for label, answer in (("A_grounded", analysis.answer), ("B_naive", naive)):
        claims = [
            c for c in _with_retry(
                lambda: extract_and_filter(llm, answer, gt, label), label=f"{replay_id}: judge {label}"
            )
            if c.claim_type == "damage_range"
        ]
        log_verdicts = [verify_damage_claim_against_log(c, hits) for c in claims]
        entry: dict[str, Any] = {
            "answer": answer,
            "damage_claims": _tally(log_verdicts),
            "claims": [
                {"claim": v.claim.model_dump(), "log_verdict": v.verdict, "reason": v.reason}
                for v in log_verdicts
            ],
        }
        if label == "A_grounded":
            entry["circularity"] = circularity_matrix([verify_claim(c, gt) for c in claims], log_verdicts)
        result[label] = entry
        print(f"  {label}: {entry['damage_claims']}")
    # Answers and extracted claims often quote the real player names.
    players = [str(p) for p in replay.get("players", [])]
    anonymized: dict[str, Any] = json.loads(anonymize(json.dumps(result, ensure_ascii=False), players))
    return anonymized


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Counter[str]] = {k: Counter() for k in ("A_grounded", "B_naive")}
    circularity: Counter[str] = Counter()
    for r in results:
        for label in totals:
            totals[label].update(r[label]["damage_claims"])
        circularity.update(r["A_grounded"]["circularity"])
    a, b = totals["A_grounded"], totals["B_naive"]
    fisher = fisher_exact_2x2(a["correct"], a["incorrect"], b["correct"], b["incorrect"])
    return {
        "replays": len(results),
        "A_grounded": dict(a),
        "B_naive": dict(b),
        "fisher_exact_on_log_verified_damage_claims": fisher.summary(),
        "A_projection_vs_log_circularity": dict(sorted(circularity.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--provider", default=None, help="openai|gemini (default: config default)")
    parser.add_argument("--orchestrator", default="native", help="native (default) | langchain | adk")
    parser.add_argument("--chaos", default="firestore", help="firestore (default) | local")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    replays = load_replays(args.replays, limit=args.limit)
    if not replays:
        raise SystemExit(f"No replays in {args.replays} (see replay_corpus.py).")
    container = build_container(args.chaos)
    provider = (args.provider or container.settings.default_provider).lower()
    out = args.out or _OUT_DIR / f"log-grounded-{args.orchestrator}-{provider}-{int(time.time())}.json"
    results: list[dict[str, Any]] = []
    try:
        for replay_id, replay in replays.items():
            results.append(_run_replay(container, replay_id, replay, provider, args.orchestrator))
            report = {"provider": provider, "orchestrator": args.orchestrator,
                      "question": QUESTION, "summary": _summary(results), "replays": results}
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    finally:
        container.shutdown()
    print(json.dumps(_summary(results), indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
