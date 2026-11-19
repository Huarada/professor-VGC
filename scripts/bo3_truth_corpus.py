"""Blind-inference accuracy over every recorded BO3 whose spreads are fully known (both players).

    python -m scripts.bo3_truth_corpus --out .cache/truth-corpus.json [--search legacy] [--series 2709311277]

A series joins the corpus by adding ``bo3-public-<id>.json`` (replays) and ``bo3-truth-<id>.json``
(native spreads and natures of BOTH teams) to ``tests/fixtures/series``; see docs/bo3-inference/CORPUS.md.
Inference reads only the public file. The truth file is opened after the search ends and only scores it:

- soundness: the true point lies in the reported interval and is not one of its excluded interior
  points, and the true nature is still a candidate. Any miss is a bug, not noise;
- tightness: how many of the 33 possible points each certified bound removes (declarations remove 0);
- fragility: whether a narrowed stat rests on a single observation, and whether those were sound;
- symmetry: the same metrics per participant, to catch a bias toward the player or the opponent.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from scripts.bo3_real_series_benchmark import FIXTURES, ROOT, collect, held_out_profiles
from scripts.faithfulness_benchmark.stats import format_rate, wilson_interval
from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.models import CandidateProfile, CandidateSummary, DeclaredSet, InferenceOptions
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator

POINTS = 33  # native Champions points 0-32
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


@dataclass(frozen=True)
class StatScore:
    series: str
    game: int
    pokemon_id: str
    participant: str
    species: str
    stat: str
    truth: int
    low: int
    high: int
    excluded: tuple[int, ...]
    sound: bool
    removed: int
    fragile: bool


def discover(directory: Path = FIXTURES) -> dict[str, tuple[Path, Path]]:
    """Series id -> (public replays, truth spreads) for every complete pair."""
    found = {}
    for public in sorted(directory.glob("bo3-public-*.json")):
        series = public.stem.removeprefix("bo3-public-")
        truth = directory / f"bo3-truth-{series}.json"
        if truth.is_file():
            found[series] = (public, truth)
    return found


def validate_truth(truth: Mapping[str, Any], initial: Mapping[str, DeclaredSet]) -> list[str]:
    """Problems that would make a truth file unusable as symmetric full information."""
    problems = []
    teams = truth.get("teams", {})
    for key, entry in initial.items():
        known = teams.get(entry.participant, {}).get(entry.species)
        if known is None:
            problems.append(f"{key}: no truth for {entry.participant}/{entry.species}")
            continue
        points = known.get("points", {})
        if set(points) != set(STATS) or any(not isinstance(v, int) or not 0 <= v <= 32 for v in points.values()):
            problems.append(f"{key}: points must give all six stats as integers 0-32")
        elif sum(points.values()) > 66:
            problems.append(f"{key}: points sum to {sum(points.values())}, above the 66 cap")
        if not known.get("nature"):
            problems.append(f"{key}: missing nature")
    registered = {(e.participant, e.species) for e in initial.values()}
    for participant, team in teams.items():
        problems += [f"{participant}/{species}: truth for a Pokemon the replays never registered"
                     for species in team if (participant, species) not in registered]
    if len({participant for participant, _ in registered}) != 2:
        problems.append("a series needs both players' teams")
    return problems


def score_checkpoint(series: str, game: int, summaries: Iterable[CandidateSummary],
                     profiles: Mapping[str, CandidateProfile]) -> list[StatScore]:
    scores = []
    for summary in summaries:
        truth = profiles[summary.pokemon_id]
        for stat, bound in summary.stat_points.items():
            value = truth.points[stat]
            holes = tuple(summary.excluded_points.get(stat, ()))
            width = bound.maximum - bound.minimum + 1
            scores.append(StatScore(
                series=series, game=game, pokemon_id=summary.pokemon_id,
                participant=summary.pokemon_id.rsplit(":slot", 1)[0], species=summary.species, stat=stat,
                truth=value, low=bound.minimum, high=bound.maximum, excluded=holes,
                sound=bound.minimum <= value <= bound.maximum and value not in holes,
                removed=POINTS - width + len(holes), fragile=stat in summary.single_observation_bounds))
    return scores


def nature_misses(summaries: Iterable[CandidateSummary], profiles: Mapping[str, CandidateProfile]) -> list[str]:
    return [s.pokemon_id for s in summaries if profiles[s.pokemon_id].natures[0] not in s.natures]


def run_series(adapter: SmogonCandidateProjectionAdapter, series: str, public: Path, truth_path: Path,
               search: Literal["propagation", "legacy"]) -> dict[str, Any]:
    games = json.loads(public.read_text(encoding="utf-8"))["games"]
    request, evidence, states, sheet, initial, _hits, _skipped = collect(adapter, games)
    if request.teamsheet is not None or any(entry.stat_points for entry in sheet.pokemon):
        raise ValueError(f"{series}: public input exposes hidden native points")
    options = InferenceOptions(max_projections=12000, max_assignments=12000, timeout_seconds=120, search=search)
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), public_sheets=ShowdownTeamSheetParser(),
        projection=adapter).analyze(request.model_copy(update={"inference_options": options}), evidence, states)
    if result is None:
        raise ValueError(f"{series}: public inference produced no checkpoints")
    # Truth is read only now, after the blind search finished.
    problems = validate_truth(json.loads(truth_path.read_text(encoding="utf-8")), initial)
    if problems:
        return {"series": series, "invalid_truth": problems}
    profiles = held_out_profiles(initial, truth_path)
    scores: list[StatScore] = []
    natures: list[str] = []
    for checkpoint in result.checkpoints:
        if checkpoint.phase == "after_game":
            scores += score_checkpoint(series, checkpoint.game, checkpoint.summaries, profiles)
            natures += nature_misses(checkpoint.summaries, profiles)
    return {"series": series, "games": len(evidence.games), "projections": result.projections,
            "coverage": [c.coverage for c in result.checkpoints if c.phase == "after_game"],
            "scores": [asdict(s) for s in scores], "nature_misses": natures}


def guarded(adapter: SmogonCandidateProjectionAdapter, series: str, public: Path, truth_path: Path,
            search: Literal["propagation", "legacy"]) -> dict[str, Any]:
    """One series that fails is reported and counted, never allowed to hide the rest of the corpus."""
    try:
        return run_series(adapter, series, public, truth_path, search)
    except Exception as exc:  # noqa: BLE001 - a benchmark must record every failure kind
        return {"series": series, "error": f"{type(exc).__name__}: {exc}"}


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    hits = sum(bool(r[key]) for r in rows)
    low, high = wilson_interval(hits, len(rows))
    return {"count": hits, "of": len(rows), "text": format_rate(hits, len(rows)), "ci": [round(low, 3), round(high, 3)]}


def _group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"soundness": _rate(rows, "sound"), "narrowed": _rate(rows, "narrowed"),
            "mean_removed": round(sum(r["removed"] for r in rows) / len(rows) / POINTS, 3)}


def summarize(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Final-checkpoint headline plus per-stat and per-participant breakdowns, pooled over series."""
    final: list[dict[str, Any]] = []
    for run in runs:
        rows = run.get("scores", [])
        last = max((r["game"] for r in rows), default=0)
        final += [{**r, "narrowed": r["removed"] > 0} for r in rows if r["game"] == last]
    narrowed = [r for r in final if r["narrowed"]]
    by_stat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in final:
        by_stat[r["stat"]].append(r)
        by_player[r["series"] + ":" + r["participant"]].append(r)
    removed = sum(r["removed"] for r in final) / len(final) / POINTS if final else None
    return {
        "series": len(runs), "pokemon_stats": len(final),
        "soundness": _rate(final, "sound") if final else None,
        "soundness_of_narrowed": _rate(narrowed, "sound") if narrowed else None,
        "narrowed_stats": _rate(final, "narrowed") if final else None,
        "mean_fraction_of_range_removed": round(removed, 3) if removed is not None else None,
        "fragile_among_narrowed": _rate(narrowed, "fragile") if narrowed else None,
        "unsound": [f'{r["series"]} {r["pokemon_id"]} {r["species"]} {r["stat"]}: truth {r["truth"]} vs '
                    f'{r["low"]}-{r["high"]} minus {list(r["excluded"])}' for r in final if not r["sound"]],
        "nature_misses": [m for run in runs for m in run.get("nature_misses", [])],
        "invalid_truth": [p for run in runs for p in run.get("invalid_truth", [])],
        "errors": [f'{run["series"]}: {run["error"]}' for run in runs if "error" in run],
        "per_stat": {stat: _group(rows) for stat, rows in by_stat.items()},
        "per_participant": {name: _group(rows) for name, rows in by_player.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--search", choices=("propagation", "legacy"), default="propagation")
    parser.add_argument("--series", action="append", help="limit to these series ids")
    parser.add_argument("--dir", type=Path, default=FIXTURES, help="folder with bo3-public/bo3-truth pairs")
    parser.add_argument("--limit", type=int, default=0, help="run at most N series (0 = all)")
    args = parser.parse_args()
    corpus = {k: v for k, v in discover(args.dir).items() if not args.series or k in args.series}
    if args.limit:
        corpus = dict(list(corpus.items())[:args.limit])
    if not corpus:
        raise SystemExit(f"No complete bo3-public/bo3-truth pair found in {args.dir}.")
    adapter = SmogonCandidateProjectionAdapter(ipc=NodeIpcClient(
        server_script=ROOT / "node_calc" / "calc_server.js", timeout_seconds=15))
    try:
        runs = [guarded(adapter, series, public, truth, args.search) for series, (public, truth) in corpus.items()]
    finally:
        adapter.close()
    summary = summarize(runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"search": args.search, "summary": summary, "runs": runs}, indent=1) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 1 if summary["unsound"] or summary["nature_misses"] or summary["invalid_truth"] or summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
