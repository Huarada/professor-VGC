"""Numeric error metrics for damage_range claims — a sharper instrument than
the binary correct/incorrect verdict verify.py uses for the main benchmark.

`verify.py`'s damage_range check answers "did the claim fall within the real
range (+/- 3pp)?" — a yes/no. This script answers "by how much was it off?",
using every ALREADY-COLLECTED claim from a run's saved JSON (no new LLM
calls: the claims were extracted once when the run happened). Ground truth
is rebuilt fresh and LLM-free for each fixture: ShowdownReplayParser (no LLM)
+ a comprehensive fallback_plan() covering every cross-side pair (no 1st-AI
call needed, since TurnReplaySimulator already covers every real move
regardless of selection, and fallback_plan gives MatchupEvaluator full
cross-side coverage too) + MatchupEvaluator + TurnReplaySimulator (both
deterministic, zero LLM calls).

Reports, per condition:
  - MSRE  (mean squared relative error)      = mean( ((pred-real)/real)^2 )
  - RMSRE (root of the above, same units as relative error, easier to read)
  - MAE   (mean absolute error, percentage points)
  - RMSE  (root mean squared error, percentage points)

Relative-error metrics (MSRE/RMSRE) are reported ALONGSIDE the absolute ones
deliberately: a relative metric is skewed by any low-magnitude ground-truth
value (a real 2% vs a claimed 5% is a 150% relative error despite being
just 3 percentage points off) — exactly the kind of distortion an absolute
metric in the domain's own natural unit (percentage points of HP) does not
have. Reporting both is more honest than picking the one that tells a
cleaner story.

Usage:
    python -m scripts.faithfulness_benchmark.damage_error_metrics [run.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import MetaContext
from src.services.container import Container
from src.services.matchup_evaluator import MatchupEvaluator
from src.services.selection_logic import fallback_plan
from src.services.turn_simulator import TurnReplaySimulator

from scripts.faithfulness_benchmark.fixtures import FIXTURES
from scripts.faithfulness_benchmark.ground_truth import GroundTruth, resolve_species

_DEFAULT_RUN = Path(__file__).resolve().parent / "out" / "run3.json"


def _comprehensive_ground_truth(container: Container, fixture) -> GroundTruth:
    """Rebuild GroundTruth with FULL cross-side matchup coverage, with zero
    LLM calls — TurnReplaySimulator already covers every real move regardless
    of selection; fallback_plan() gives MatchupEvaluator the same full
    coverage a real 1st-AI selection would only partially provide."""
    game_state = ShowdownReplayParser().parse(fixture.replay)
    calc = container.calc_engine()
    meta = container.chaos().build_match_context(
        game_state.involved_species(), metagame=game_state.format_id, rating=game_state.rating
    )
    side_of = game_state.side_of()
    plan = fallback_plan(list(side_of.keys()), side_of)
    evaluator = MatchupEvaluator(calc, container.settings.calc_gen)
    simulator = TurnReplaySimulator(calc, container.settings.calc_gen)
    verdicts = evaluator.evaluate(game_state, plan, meta)
    turn_checks = simulator.simulate(game_state, meta)

    # Reuse GroundTruth's own aggregation logic by handing it a stand-in
    # object with just the two fields it reads (turn_checks, verdicts) —
    # avoids depending on the full AnalysisResult shape for this analysis.
    class _StandIn:
        pass

    stand_in = _StandIn()
    stand_in.turn_checks = turn_checks
    stand_in.verdicts = verdicts
    return GroundTruth.build(game_state, stand_in)  # type: ignore[arg-type]


def _real_midpoint(gt: GroundTruth, attacker_text: str, defender_text: str, move_text: str) -> float | None:
    attacker = resolve_species(attacker_text, gt.real_species)
    defender = resolve_species(defender_text, gt.real_species)
    move = move_text.lower().replace(" ", "").replace("-", "").replace("'", "")
    if attacker is None or defender is None:
        return None
    ranges = gt.damage_ranges.get((attacker, defender, move))
    if not ranges:
        return None
    midpoints = [(lo + hi) / 2 for lo, hi in ranges]
    return mean(midpoints)


def compute(run_path: Path) -> None:
    data = json.loads(run_path.read_text(encoding="utf-8"))
    container = Container()
    gt_by_fixture = {fx.id: _comprehensive_ground_truth(container, fx) for fx in FIXTURES}

    errors: dict[str, list[tuple[float, float]]] = {"A_grounded": [], "B_naive": []}
    skipped = {"A_grounded": 0, "B_naive": 0}

    for fr in data["report"]["fixtures"]:
        gt = gt_by_fixture[fr["fixture_id"]]
        for cond_key, label in (("condition_a", "A_grounded"), ("condition_b", "B_naive")):
            for c in fr[cond_key]["claims"]:
                claim = c["claim"]
                if claim["claim_type"] != "damage_range":
                    continue
                if claim.get("min_percent") is None or claim.get("max_percent") is None:
                    skipped[label] += 1
                    continue
                real_mid = _real_midpoint(gt, claim["attacker"], claim["defender"], claim["move"])
                if real_mid is None:
                    skipped[label] += 1
                    continue
                pred_mid = (claim["min_percent"] + claim["max_percent"]) / 2
                errors[label].append((pred_mid, real_mid))

    print(f"Source: {run_path.name}\n")
    for label in ("A_grounded", "B_naive"):
        pairs = errors[label]
        n = len(pairs)
        if n == 0:
            print(f"{label}: no comparable damage_range claims")
            continue
        abs_errors = [abs(p - r) for p, r in pairs]
        sq_errors = [(p - r) ** 2 for p, r in pairs]
        # Relative error is undefined at real=0; none should remain after the
        # type-immunity fixture fix, but guard anyway rather than crash.
        rel_sq_errors = [((p - r) / r) ** 2 for p, r in pairs if r != 0]
        n_rel = len(rel_sq_errors)

        mae = mean(abs_errors)
        rmse = mean(sq_errors) ** 0.5
        msre = mean(rel_sq_errors) if rel_sq_errors else float("nan")
        rmsre = msre ** 0.5 if rel_sq_errors == rel_sq_errors and n_rel else float("nan")

        print(f"{label} (n={n}, skipped={skipped[label]} unresolvable/no-ground-truth):")
        print(f"  MAE   = {mae:6.2f} pp")
        print(f"  RMSE  = {rmse:6.2f} pp")
        print(f"  MSRE  = {msre:6.4f}  (n={n_rel})")
        print(f"  RMSRE = {rmsre:6.4f}  ({rmsre*100:.1f}% typical relative error)")
        print()

    container.shutdown()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_RUN
    compute(path)
