"""Re-score a saved log-grounded run under another tolerance — no LLM calls.

    python -m scripts.faithfulness_benchmark.rescore_log_grounded \
        scripts/faithfulness_benchmark/out/log_grounded_n20_openai.json \
        [--replays data/replays/cache] [--relative-tolerance 0.05] [--write]

Re-judges every stored claim against the observed damage of its replay and
prints the summary with 95% intervals and the sensitivity table. ``--write``
replaces the report's ``summary`` in place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from scripts.faithfulness_benchmark.log_grounded_scoring import summarize
from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
from scripts.faithfulness_benchmark.replay_corpus import DEFAULT_CACHE_DIR, load_replays
from scripts.faithfulness_benchmark.tolerance import ToleranceBand


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--replays", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--relative-tolerance", type=float, default=0.05)
    parser.add_argument("--write", action="store_true", help="update the report's summary in place")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    replays = load_replays(args.replays)
    missing = [r["replay_id"] for r in report["replays"] if r["replay_id"] not in replays]
    if missing:
        raise SystemExit(f"Replays not in {args.replays}: {missing} (see replay_corpus.py)")
    hits = {rid: extract_observed_hits(str(replays[rid]["log"])) for rid in replays}
    summary = summarize(report["replays"], hits, ToleranceBand(relative=args.relative_tolerance))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.write:
        report["summary"] = summary
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"updated {args.report}")


if __name__ == "__main__":
    main()
