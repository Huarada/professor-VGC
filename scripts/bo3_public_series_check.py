"""Blind inference over real public BO3 series whose spreads are NOT published (no truth file).

    python -m scripts.bo3_public_series_check --out .cache/public-series-check.json

Accuracy cannot be scored without hidden spreads. This checks what real games can still prove:
every series parses and validates, inference finishes, and no checkpoint reports a ``conflict``
(a real game always has at least one legal spread, so a conflict is a parser/engine bug or an
unmodelled mechanic). It also reports how much real data narrows, to compare with the simulated corpus.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.bo3_real_series_benchmark import FIXTURES, ROOT, collect
from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.models import InferenceOptions
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator

POINTS = 33


def discover(directory: Path = FIXTURES) -> dict[str, Path]:
    """Public-only series: showteam fixtures that have no truth file."""
    found = {}
    for public in sorted(directory.glob("bo3-public-showteam-*.json")):
        series = public.stem.removeprefix("bo3-public-showteam-")
        if not (directory / f"bo3-truth-{series}.json").exists():
            found[series] = public
    return found


def run_series(adapter: SmogonCandidateProjectionAdapter, series: str, public: Path) -> dict[str, Any]:
    games = json.loads(public.read_text(encoding="utf-8"))["games"]
    request, evidence, states, _sheet, _initial, hits, _skipped = collect(adapter, games)
    options = InferenceOptions(max_projections=12000, max_assignments=12000, timeout_seconds=120)
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), public_sheets=ShowdownTeamSheetParser(),
        projection=adapter).analyze(request.model_copy(update={"inference_options": options}), evidence, states)
    if result is None:
        raise ValueError("public inference produced no checkpoints")
    final = [c for c in result.checkpoints if c.phase == "after_game"][-1]
    stats = [(s, r) for s in final.summaries for r in s.stat_points.values()]
    narrowed = sum(r.maximum - r.minimum + 1 < POINTS for _s, r in stats)
    holes = sum(len(v) for s in final.summaries for v in s.excluded_points.values())
    return {"series": series, "games": len(evidence.games), "hits": len(hits), "projections": result.projections,
            "outcomes": [c.outcome for c in result.checkpoints if c.phase == "after_game"],
            "coverage": [c.coverage for c in result.checkpoints if c.phase == "after_game"],
            "stats": len(stats), "narrowed": narrowed, "interior_holes": holes}


def guarded(adapter: SmogonCandidateProjectionAdapter, series: str, public: Path) -> dict[str, Any]:
    try:
        return run_series(adapter, series, public)
    except Exception as exc:  # noqa: BLE001 - one bad series must not hide the others
        return {"series": series, "error": f"{type(exc).__name__}: {exc}"}


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in runs if "error" not in r]
    outcomes = Counter(o for r in done for o in r["outcomes"][-1:])
    return {"series": len(runs), "errors": [f"{r['series']}: {r['error']}" for r in runs if "error" in r],
            "conflicts": [r["series"] for r in done if "conflict" in r["outcomes"]],
            "final_outcomes": dict(outcomes), "stats": sum(r["stats"] for r in done),
            "narrowed": sum(r["narrowed"] for r in done), "interior_holes": sum(r["interior_holes"] for r in done)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dir", type=Path, default=FIXTURES)
    args = parser.parse_args()
    corpus = discover(args.dir)
    if not corpus:
        raise SystemExit(f"No public-only series in {args.dir}.")
    adapter = SmogonCandidateProjectionAdapter(ipc=NodeIpcClient(
        server_script=ROOT / "node_calc" / "calc_server.js", timeout_seconds=15))
    try:
        runs = [guarded(adapter, series, public) for series, public in corpus.items()]
    finally:
        adapter.close()
    summary = summarize(runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "runs": runs}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 1 if summary["errors"] or summary["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
