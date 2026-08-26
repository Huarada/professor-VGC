"""Run the faithfulness mini-benchmark end to end.

    python -m scripts.faithfulness_benchmark.run [--provider openai] [--out FILE.json]

For each fixture: parses the replay once (for ground truth), runs the real
native pipeline (Condition A), runs the ungrounded baseline (Condition B),
judges both answers' claims with a separate LLM call, verifies every claim
deterministically against ground truth, and prints/saves a faithfulness-rate
comparison table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `src`/`scripts` imports

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest
from src.services.container import Container

from scripts.faithfulness_benchmark.damage_dense_fixtures import DAMAGE_DENSE_FIXTURES
from scripts.faithfulness_benchmark.fixtures import FIXTURES, Fixture
from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.judge import extract_claims
from scripts.faithfulness_benchmark.models import (
    AtomicClaim,
    BenchmarkReport,
    ConditionResult,
    FixtureResult,
)
from scripts.faithfulness_benchmark.naive_baseline import run_naive_baseline
from scripts.faithfulness_benchmark.stats import FisherResult, fisher_exact_2x2
from scripts.faithfulness_benchmark.verify import (
    filter_ambiguous_damage_claims,
    filter_degenerate_claims,
    verify_claim,
)

# 10 trap-category fixtures (fixtures.py) + 20 damage-dense fixtures
# (damage_dense_fixtures.py, engineered specifically to maximize genuine
# damage_range claims per replay). damage_range is this benchmark's
# statistically headline metric (see README's "Primary result" section) —
# per the same principle CLAUDE.md already states for this codebase's own
# fixture priorities, growing n where the effect actually lives is worth
# more than growing it with fixtures whose claims mostly fall into
# categories both conditions already tie on.
ALL_FIXTURES: list[Fixture] = FIXTURES + DAMAGE_DENSE_FIXTURES


def _extract_and_filter(llm, text: str, gt: GroundTruth, label: str) -> list[AtomicClaim]:
    """extract_claims() followed by both deterministic post-extraction
    guards, with a print per dropped category so a run's console output
    stays a legible audit trail of what the judge got wrong and why it
    didn't count against either condition."""
    claims, dropped_trainer = filter_degenerate_claims(extract_claims(llm, text), gt)
    if dropped_trainer:
        print(f"  [filter] dropped {dropped_trainer} trainer-named claim(s) ({label})")
    claims, dropped_ambiguous = filter_ambiguous_damage_claims(claims)
    if dropped_ambiguous:
        print(f"  [filter] dropped {dropped_ambiguous} non-damage-dealt percent claim(s) ({label})")
    return claims

_OUT_DIR = Path(__file__).resolve().parent / "out"


def _run_fixture(container: Container, fixture: Fixture) -> FixtureResult:
    print(f"=== {fixture.id} ({', '.join(fixture.tags)}) ===")

    # Ground truth, condition-independent: parse once, run the real pipeline
    # once. Both conditions are judged against this SAME ground truth.
    game_state = ShowdownReplayParser().parse(fixture.replay)
    pipeline = container.build_pipeline(orchestrator="native")
    request = AnalysisRequest(
        session_id=f"bench-{fixture.id}",
        replay_json=fixture.replay,
        question=fixture.question,
    )
    analysis = pipeline.analyze(request)
    gt = GroundTruth.build(game_state, analysis)

    llm = container.build_llm()

    # --- Condition A: the real pipeline's own answer ---
    claims_a = _extract_and_filter(llm, analysis.answer, gt, "A")
    verdicts_a = [verify_claim(c, gt) for c in claims_a]
    cond_a = ConditionResult(condition="A_grounded", answer=analysis.answer, claims=verdicts_a)
    print(f"  A (grounded):  {len(claims_a)} claims, "
          f"strict={cond_a.strict_rate}, lenient={cond_a.lenient_rate}")

    # --- Condition B: raw log, no grounding ---
    naive_answer = run_naive_baseline(llm, fixture.replay["log"], fixture.question)
    claims_b = _extract_and_filter(llm, naive_answer, gt, "B")
    verdicts_b = [verify_claim(c, gt) for c in claims_b]
    cond_b = ConditionResult(condition="B_naive", answer=naive_answer, claims=verdicts_b)
    print(f"  B (naive):     {len(claims_b)} claims, "
          f"strict={cond_b.strict_rate}, lenient={cond_b.lenient_rate}")

    return FixtureResult(fixture_id=fixture.id, tags=fixture.tags, condition_a=cond_a, condition_b=cond_b)


def _counts(claims, claim_type: str | None = None) -> tuple[int, int, int]:
    """(correct, incorrect, unverifiable) counts, optionally restricted to
    one claim_type — damage_range is what this benchmark's statistics are
    actually built on; the aggregate over every type mixes categories with
    a real effect (damage_range) and categories with none (move_used,
    pokemon_played, winner, ... — see README's per-claim-type table), which
    is why the aggregate is reported as context, not the headline."""
    if claim_type is not None:
        claims = [c for c in claims if c.claim.claim_type == claim_type]
    correct = sum(1 for c in claims if c.verdict == "correct")
    incorrect = sum(1 for c in claims if c.verdict == "incorrect")
    unverifiable = sum(1 for c in claims if c.verdict == "unverifiable")
    return correct, incorrect, unverifiable


def _rate_dict(correct: int, incorrect: int, unverifiable: int) -> dict:
    total = correct + incorrect + unverifiable
    verifiable = correct + incorrect
    return {
        "total_claims": total,
        "correct": correct,
        "incorrect": incorrect,
        "unverifiable": unverifiable,
        "strict_faithfulness_rate": (correct / total) if total else None,
        "lenient_faithfulness_rate": (correct / verifiable) if verifiable else None,
    }


def _aggregate(report: BenchmarkReport) -> dict:
    a_claims = [c for fr in report.fixtures for c in fr.condition_a.claims]
    b_claims = [c for fr in report.fixtures for c in fr.condition_b.claims]

    a_dmg_correct, a_dmg_incorrect, a_dmg_unverif = _counts(a_claims, "damage_range")
    b_dmg_correct, b_dmg_incorrect, b_dmg_unverif = _counts(b_claims, "damage_range")
    fisher = fisher_exact_2x2(a_dmg_correct, a_dmg_incorrect, b_dmg_correct, b_dmg_incorrect)

    return {
        "primary_result_damage_range": {
            "condition_a_grounded": _rate_dict(a_dmg_correct, a_dmg_incorrect, a_dmg_unverif),
            "condition_b_naive": _rate_dict(b_dmg_correct, b_dmg_incorrect, b_dmg_unverif),
            "fisher_exact_test": {
                "odds_ratio": fisher.odds_ratio,
                "p_two_sided": fisher.p_two_sided,
                "p_one_sided_a_greater_than_b": fisher.p_one_sided_greater,
                "significant_at_0.05": fisher.p_two_sided < 0.05,
            },
        },
        "secondary_context_all_claim_types": {
            "condition_a_grounded": _rate_dict(*_counts(a_claims)),
            "condition_b_naive": _rate_dict(*_counts(b_claims)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default=None, help="openai|gemini (default: config default)")
    parser.add_argument("--out", default=None, help="output JSON path (default: out/report-<ts>.json)")
    parser.add_argument("--limit", type=int, default=None, help="only run the first N fixtures")
    parser.add_argument(
        "--trap-fixtures-only", action="store_true",
        help="use only the original 10 trap-category fixtures, not the 20 damage-dense ones",
    )
    args = parser.parse_args()

    container = Container()
    provider_name = args.provider or container.settings.default_provider
    fixtures = FIXTURES if args.trap_fixtures_only else ALL_FIXTURES
    if args.limit:
        fixtures = fixtures[: args.limit]

    results = [_run_fixture(container, f) for f in fixtures]
    report = BenchmarkReport(
        provider=provider_name,
        model=container.settings.openai_model if provider_name == "openai" else container.settings.gemini_model,
        fixtures=results,
    )

    summary = _aggregate(report)
    dmg = summary["primary_result_damage_range"]
    fisher_result = FisherResult(
        a_correct=dmg["condition_a_grounded"]["correct"],
        a_incorrect=dmg["condition_a_grounded"]["incorrect"],
        b_correct=dmg["condition_b_naive"]["correct"],
        b_incorrect=dmg["condition_b_naive"]["incorrect"],
        odds_ratio=dmg["fisher_exact_test"]["odds_ratio"],
        p_two_sided=dmg["fisher_exact_test"]["p_two_sided"],
        p_one_sided_greater=dmg["fisher_exact_test"]["p_one_sided_a_greater_than_b"],
    )
    print(f"\n=== PRIMARY RESULT: damage_range (n={len(fixtures)} fixtures) ===")
    print(fisher_result.summary())
    print("\n=== secondary context: all claim types combined ===")
    ctx = summary["secondary_context_all_claim_types"]
    print(f"A: {ctx['condition_a_grounded']['correct']}/{ctx['condition_a_grounded']['total_claims']} "
          f"({ctx['condition_a_grounded']['strict_faithfulness_rate']:.1%})  vs  "
          f"B: {ctx['condition_b_naive']['correct']}/{ctx['condition_b_naive']['total_claims']} "
          f"({ctx['condition_b_naive']['strict_faithfulness_rate']:.1%})  "
          "-- mixes categories with and without an effect; not the headline, see README")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else _OUT_DIR / f"report-{int(time.time())}.json"
    out_path.write_text(
        json.dumps({"summary": summary, "report": report.model_dump()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nFull report written to {out_path}")

    container.shutdown()


if __name__ == "__main__":
    main()
