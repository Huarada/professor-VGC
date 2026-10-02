"""Re-score saved log-grounded runs under any tolerance — no LLM calls.

    python -m scripts.faithfulness_benchmark.rescore_log_grounded \
        out/run_games1-20.json [out/run_games21-40.json ...] \
        [--replays data/replays/cache] [--relative-tolerance 0.05] [--write] [--out FILE.json]

Re-judges every stored claim against the observed damage of its replay and
prints the summary with 95% intervals and the sensitivity table. Several
reports are POOLED only when they cover different games (the same game
counted twice would overstate the sample); overlapping reports are refused.
``--write`` replaces a single report's ``summary`` in place; ``--out`` saves
the (pooled) summary.
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
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--replays", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--relative-tolerance", type=float, default=0.05)
    parser.add_argument("--write", action="store_true",
                        help="update the summary in place (single report only)")
    parser.add_argument("--out", type=Path, default=None, help="save the (pooled) summary here")
    args = parser.parse_args()

    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    results = [result for report in reports for result in report["replays"]]
    ids = [r["replay_id"] for r in results]
    duplicated = sorted({rid for rid in ids if ids.count(rid) > 1})
    if duplicated:
        raise SystemExit(f"Refusing to pool reports that share games: {duplicated}")
    replays = load_replays(args.replays)
    missing = [rid for rid in ids if rid not in replays]
    if missing:
        raise SystemExit(f"Replays not in {args.replays}: {missing} (see replay_corpus.py)")
    hits = {rid: extract_observed_hits(str(replays[rid]["log"])) for rid in ids}
    summary = summarize(results, hits, ToleranceBand(relative=args.relative_tolerance))
    summary["reports"] = [path.as_posix() for path in args.reports]
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.write:
        if len(reports) != 1:
            raise SystemExit("--write needs exactly one report")
        reports[0]["summary"] = summary
        args.reports[0].write_text(json.dumps(reports[0], indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"updated {args.reports[0]}")
    if args.out:
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"saved {args.out}")


if __name__ == "__main__":
    main()
