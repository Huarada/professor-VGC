"""Run the faithfulness mini-benchmark's Condition A through EVERY
orchestration backend (adk / langchain / native) against the SAME ground
truth and the SAME naive baseline (Condition B), then compare their
`damage_range` precision pairwise with Fisher's exact test.

    python -m scripts.faithfulness_benchmark.run_orchestrator_comparison \
        [--orchestrators adk,langchain,native] [--provider openai] \
        [--out FILE.json] [--limit N] [--trap-fixtures-only]

Deliberately a SEPARATE script from `run.py`, not a `--orchestrator` flag
bolted onto it: `run.py`'s `FixtureResult`/`BenchmarkReport` models
(`scripts/faithfulness_benchmark/models.py`) have a fixed two-condition
(A_grounded/B_naive) shape that every other tool in this benchmark
(`damage_error_metrics.py`'s raw-JSON reader excepted — that one is
condition-key-agnostic and works unchanged against this script's own output
too) and its own tests depend on; reusing it for an N-way comparison would
mean either breaking that shape or bolting an awkward "orchestrator" field
onto a model that was never designed to vary along that axis. This script
defines its own small, local report shape instead (below) and reuses every
other already-verified piece (fixtures, ground truth, the judge, the
deterministic verifier, the Fisher's-exact-test helper) unchanged.

Cost note: Condition B (the ungrounded baseline) does not depend on the
orchestrator at all — see `naive_baseline.py`, which talks to a bare
`LLMProvider`, never an `AnalysisPipeline` — so it is computed exactly ONCE
per fixture and reused for every orchestrator's comparison, rather than
re-run per orchestrator (which `run.py --orchestrator X` three times would
do). This is both cheaper (saves ~2/3 of the naive-baseline + judge-B calls
a naive three-separate-runs approach would cost) and a more valid
apples-to-apples comparison: every orchestrator's Condition A is judged
against the exact same ground truth AND the exact same B sample, so any
precision difference found is attributable to the orchestrator, not to
naive-baseline sampling noise between separate runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `src`/`scripts` imports

from pydantic import BaseModel, Field

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest
from src.services.container import Container

from scripts.faithfulness_benchmark.aggregate import counts, extract_and_filter, rate_dict
from scripts.faithfulness_benchmark.damage_dense_fixtures import DAMAGE_DENSE_FIXTURES
from scripts.faithfulness_benchmark.fixtures import FIXTURES, Fixture
from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.models import ClaimVerdict, ConditionResult
from scripts.faithfulness_benchmark.naive_baseline import run_naive_baseline
from scripts.faithfulness_benchmark.stats import fisher_exact_2x2
from scripts.faithfulness_benchmark.verify import verify_claim

ALL_FIXTURES: list[Fixture] = FIXTURES + DAMAGE_DENSE_FIXTURES
_DEFAULT_ORCHESTRATORS = ["adk", "langchain", "native"]
_OUT_DIR = Path(__file__).resolve().parent / "out"


class OrchestratorFixtureResult(BaseModel):
    """One fixture's outcome: ONE shared Condition B, and one ConditionResult
    per orchestrator (all judged against the same ground truth)."""

    fixture_id: str
    tags: list[str]
    condition_b: ConditionResult
    orchestrators: dict[str, ConditionResult] = Field(default_factory=dict)


class OrchestratorComparisonReport(BaseModel):
    provider: str
    model: str
    orchestrator_names: list[str]
    fixtures: list[OrchestratorFixtureResult] = Field(default_factory=list)


def _run_fixture(
    container: Container, fixture: Fixture, orchestrator_names: list[str]
) -> OrchestratorFixtureResult:
    print(f"=== {fixture.id} ({', '.join(fixture.tags)}) ===")

    game_state = ShowdownReplayParser().parse(fixture.replay)
    llm = container.build_llm()

    # --- Condition B: raw log, no grounding, no orchestrator involved ---
    # (naive_baseline.py talks to a bare LLMProvider, not an AnalysisPipeline
    # — computed once, shared across every orchestrator's comparison below.)
    naive_answer = run_naive_baseline(llm, fixture.replay["log"], fixture.question)

    # Ground truth is built from the FIRST orchestrator's AnalysisResult
    # (deterministic verdicts/turn-checks are identical across every
    # orchestrator by construction — see CLAUDE.md's core invariant that
    # switching orchestration technology never changes a single damage
    # roll — so any one of them is an equally valid source for GroundTruth).
    gt: GroundTruth | None = None
    per_orchestrator: dict[str, ConditionResult] = {}
    for name in orchestrator_names:
        request = AnalysisRequest(
            session_id=f"bench-{fixture.id}-{name}",
            replay_json=fixture.replay,
            question=fixture.question,
        )
        pipeline = container.build_pipeline(orchestrator=name)
        analysis = pipeline.analyze(request)
        if gt is None:
            gt = GroundTruth.build(game_state, analysis)

        claims = extract_and_filter(llm, analysis.answer, gt, name)
        verdicts = [verify_claim(c, gt) for c in claims]
        cond = ConditionResult(condition="A_grounded", answer=analysis.answer, claims=verdicts)
        per_orchestrator[name] = cond
        print(f"  {name:>9}:  {len(claims)} claims, "
              f"strict={cond.strict_rate}, lenient={cond.lenient_rate}")

    assert gt is not None  # orchestrator_names is always non-empty
    claims_b = extract_and_filter(llm, naive_answer, gt, "B")
    verdicts_b = [verify_claim(c, gt) for c in claims_b]
    cond_b = ConditionResult(condition="B_naive", answer=naive_answer, claims=verdicts_b)
    print(f"  {'B (naive)':>9}:  {len(claims_b)} claims, "
          f"strict={cond_b.strict_rate}, lenient={cond_b.lenient_rate}")

    return OrchestratorFixtureResult(
        fixture_id=fixture.id, tags=fixture.tags,
        condition_b=cond_b, orchestrators=per_orchestrator,
    )


def _all_claims(report: OrchestratorComparisonReport, name: str) -> list[ClaimVerdict]:
    if name == "B_naive":
        return [c for fr in report.fixtures for c in fr.condition_b.claims]
    return [c for fr in report.fixtures for c in fr.orchestrators[name].claims]


def _aggregate(report: OrchestratorComparisonReport) -> dict[str, Any]:
    names = report.orchestrator_names
    dmg_counts = {name: counts(_all_claims(report, name), "damage_range") for name in names}
    dmg_counts["B_naive"] = counts(_all_claims(report, "B_naive"), "damage_range")

    per_orchestrator = {
        name: rate_dict(*dmg_counts[name]) for name in [*names, "B_naive"]
    }

    # Pairwise Fisher's exact test on damage_range correct/incorrect, for
    # every orchestrator pair AND every orchestrator vs the shared B —
    # answers both "is ADK's precision different from LangChain's/native's"
    # (the comparison actually asked for) and "does each still clearly beat
    # the ungrounded baseline" (this benchmark's original primary result,
    # now reproduced per orchestrator).
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pairs.append((a, b))
    for name in names:
        pairs.append((name, "B_naive"))

    fisher_tests = {}
    for a, b in pairs:
        a_correct, a_incorrect, _ = dmg_counts[a]
        b_correct, b_incorrect, _ = dmg_counts[b]
        result = fisher_exact_2x2(a_correct, a_incorrect, b_correct, b_incorrect)
        fisher_tests[f"{a}_vs_{b}"] = {
            "odds_ratio": result.odds_ratio,
            "p_two_sided": result.p_two_sided,
            "significant_at_0.05": result.p_two_sided < 0.05,
            "summary": result.summary(),
        }

    secondary = {
        name: rate_dict(*counts(_all_claims(report, name))) for name in [*names, "B_naive"]
    }

    return {
        "primary_result_damage_range": {
            "per_condition": per_orchestrator,
            "pairwise_fisher_exact_tests": fisher_tests,
        },
        "secondary_context_all_claim_types": secondary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orchestrators", default=",".join(_DEFAULT_ORCHESTRATORS),
        help="comma-separated orchestrator names to compare (default: adk,langchain,native)",
    )
    parser.add_argument("--provider", default=None, help="openai|gemini (default: config default)")
    parser.add_argument("--out", default=None, help="output JSON path (default: out/orchestrator-comparison-<ts>.json)")
    parser.add_argument("--limit", type=int, default=None, help="only run the first N fixtures")
    parser.add_argument(
        "--trap-fixtures-only", action="store_true",
        help="use only the original 10 trap-category fixtures, not the 20 damage-dense ones",
    )
    args = parser.parse_args()
    orchestrator_names = [o.strip() for o in args.orchestrators.split(",") if o.strip()]

    container = Container()
    provider_name = args.provider or container.settings.default_provider
    fixtures = FIXTURES if args.trap_fixtures_only else ALL_FIXTURES
    if args.limit:
        fixtures = fixtures[: args.limit]

    results = [_run_fixture(container, f, orchestrator_names) for f in fixtures]
    report = OrchestratorComparisonReport(
        provider=provider_name,
        model=container.settings.openai_model if provider_name == "openai" else container.settings.gemini_model,
        orchestrator_names=orchestrator_names,
        fixtures=results,
    )

    summary = _aggregate(report)
    print(f"\n=== PRIMARY RESULT: damage_range, per orchestrator (n={len(fixtures)} fixtures) ===")
    for name, rates in summary["primary_result_damage_range"]["per_condition"].items():
        pct = f"{rates['strict_faithfulness_rate']:.1%}" if rates["strict_faithfulness_rate"] is not None else "n/a"
        print(f"  {name:>9}: {rates['correct']}/{rates['total_claims']} ({pct})")
    print("\n--- pairwise Fisher's exact tests (damage_range correct/incorrect) ---")
    for pair, result in summary["primary_result_damage_range"]["pairwise_fisher_exact_tests"].items():
        sig = "significant" if result["significant_at_0.05"] else "NOT significant"
        print(f"  {pair}: odds_ratio={result['odds_ratio']:.2f}, "
              f"p={result['p_two_sided']:.4f} -> {sig} at alpha=0.05")

    print("\n=== secondary context: all claim types combined ===")
    for name, rates in summary["secondary_context_all_claim_types"].items():
        pct = f"{rates['strict_faithfulness_rate']:.1%}" if rates["strict_faithfulness_rate"] is not None else "n/a"
        print(f"  {name:>9}: {rates['correct']}/{rates['total_claims']} ({pct})  "
              "-- mixes categories with and without an effect; not the headline")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else _OUT_DIR / f"orchestrator-comparison-{int(time.time())}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "report": report.model_dump()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nFull report written to {out_path}")

    container.shutdown()


if __name__ == "__main__":
    main()
