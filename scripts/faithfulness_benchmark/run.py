"""Run the faithfulness mini-benchmark end to end.

    python -m scripts.faithfulness_benchmark.run [--orchestrator native] [--provider openai] [--out FILE.json]

For each fixture: parses the replay once (for ground truth), runs the real
pipeline (Condition A — `--orchestrator adk|langchain|native`, default
native), runs the ungrounded baseline (Condition B), judges both answers'
claims with a separate LLM call, verifies every claim deterministically
against ground truth, and prints/saves a faithfulness-rate comparison table.

To compare Condition A's precision ACROSS orchestrators (rather than one
orchestrator vs the naive baseline) — e.g. "how does ADK differ from
LangChain/native" — use `run_orchestrator_comparison.py` instead: it holds
ground truth and the naive baseline constant across orchestrators within one
pass and reports pairwise Fisher's-exact-test results directly, which is
both cheaper and a more valid comparison than diffing three separate runs of
this script.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Callable, TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `src`/`scripts` imports

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import AnalysisRequest
from src.services.container import Container

from scripts.faithfulness_benchmark.aggregate import counts, extract_and_filter, rate_dict
from scripts.faithfulness_benchmark.damage_dense_fixtures import DAMAGE_DENSE_FIXTURES
from scripts.faithfulness_benchmark.fixtures import FIXTURES, Fixture
from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.models import (
    BenchmarkReport,
    ConditionResult,
    FixtureResult,
)
from scripts.faithfulness_benchmark.naive_baseline import run_naive_baseline
from scripts.faithfulness_benchmark.stats import FisherResult, fisher_exact_2x2
from scripts.faithfulness_benchmark.verify import verify_claim

# 10 trap-category fixtures (fixtures.py) + 20 damage-dense fixtures
# (damage_dense_fixtures.py, engineered specifically to maximize genuine
# damage_range claims per replay). damage_range is this benchmark's
# statistically headline metric (see README's "Primary result" section) —
# per the same principle CLAUDE.md already states for this codebase's own
# fixture priorities, growing n where the effect actually lives is worth
# more than growing it with fixtures whose claims mostly fall into
# categories both conditions already tie on.
ALL_FIXTURES: list[Fixture] = FIXTURES + DAMAGE_DENSE_FIXTURES


_OUT_DIR = Path(__file__).resolve().parent / "out"

_T = TypeVar("_T")

# Gemini (and OpenAI) occasionally answer a single call with a transient
# server-side error (observed live: `google.genai.errors.ServerError: 503
# UNAVAILABLE ... "This model is currently experiencing high demand"`) even
# after the SDK's own internal retry budget is exhausted, and on a sustained
# high-demand window a handful of calls in a row can all 503. Retry at the
# call site with backoff so a transient blip costs seconds, not the whole
# run — and see `main()`'s incremental checkpointing for what happens when a
# fixture still fails after exhausting this budget (it's skipped, not fatal).
_MAX_ATTEMPTS = 6
_BACKOFF_SECONDS = (3.0, 8.0, 15.0, 30.0, 45.0)

# Belt-and-suspenders on top of _with_retry: observed live, one fixture
# stalled for 30+ minutes with no exception ever raised (no [retry] line, no
# 503 — plausibly a long tool-calling back-and-forth inside the agent loop
# rather than a single retriable call), while the exact same fixture run in
# isolation finished in under a minute. _with_retry can only bound a call
# that actually raises; this bounds wall-clock time regardless of why a
# fixture is stuck, so one anomalous fixture can't stall a ~30-fixture run
# for the rest of the run. The thread is not killed on timeout (Python
# can't do that safely) — it's abandoned and the main loop moves on; the
# leaked thread eventually finishes or errors on its own and is discarded.
_FIXTURE_WALL_CLOCK_TIMEOUT_SECONDS = 360.0


def _with_retry(call: Callable[[], _T], *, label: str) -> _T:
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - transient SDK/network errors of many types
            last_exc = exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            print(f"  [retry] {label} failed ({exc}); retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{_MAX_ATTEMPTS})")
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _run_fixture(container: Container, fixture: Fixture, orchestrator: str, provider: str) -> FixtureResult:
    print(f"=== {fixture.id} ({', '.join(fixture.tags)}) ===")

    # Ground truth, condition-independent: parse once, run the real pipeline
    # once. Both conditions are judged against this SAME ground truth.
    game_state = ShowdownReplayParser().parse(fixture.replay)
    pipeline = container.build_pipeline(provider=provider, orchestrator=orchestrator)
    request = AnalysisRequest(
        session_id=f"bench-{fixture.id}",
        replay_json=fixture.replay,
        question=fixture.question,
        provider=provider,
    )
    analysis = _with_retry(lambda: pipeline.analyze(request), label=f"{fixture.id}: condition A analyze()")
    gt = GroundTruth.build(game_state, analysis)

    llm = container.build_llm(provider)

    # --- Condition A: the real pipeline's own answer ---
    claims_a = _with_retry(
        lambda: extract_and_filter(llm, analysis.answer, gt, "A"),
        label=f"{fixture.id}: condition A claim extraction",
    )
    verdicts_a = [verify_claim(c, gt) for c in claims_a]
    cond_a = ConditionResult(condition="A_grounded", answer=analysis.answer, claims=verdicts_a)
    print(f"  A (grounded):  {len(claims_a)} claims, "
          f"strict={cond_a.strict_rate}, lenient={cond_a.lenient_rate}")

    # --- Condition B: raw log, no grounding ---
    naive_answer = _with_retry(
        lambda: run_naive_baseline(llm, fixture.replay["log"], fixture.question),
        label=f"{fixture.id}: condition B naive baseline",
    )
    claims_b = _with_retry(
        lambda: extract_and_filter(llm, naive_answer, gt, "B"),
        label=f"{fixture.id}: condition B claim extraction",
    )
    verdicts_b = [verify_claim(c, gt) for c in claims_b]
    cond_b = ConditionResult(condition="B_naive", answer=naive_answer, claims=verdicts_b)
    print(f"  B (naive):     {len(claims_b)} claims, "
          f"strict={cond_b.strict_rate}, lenient={cond_b.lenient_rate}")

    return FixtureResult(fixture_id=fixture.id, tags=fixture.tags, condition_a=cond_a, condition_b=cond_b)


def _aggregate(report: BenchmarkReport) -> dict[str, Any]:
    a_claims = [c for fr in report.fixtures for c in fr.condition_a.claims]
    b_claims = [c for fr in report.fixtures for c in fr.condition_b.claims]

    a_dmg_correct, a_dmg_incorrect, a_dmg_unverif = counts(a_claims, "damage_range")
    b_dmg_correct, b_dmg_incorrect, b_dmg_unverif = counts(b_claims, "damage_range")
    fisher = fisher_exact_2x2(a_dmg_correct, a_dmg_incorrect, b_dmg_correct, b_dmg_incorrect)

    return {
        "primary_result_damage_range": {
            "condition_a_grounded": rate_dict(a_dmg_correct, a_dmg_incorrect, a_dmg_unverif),
            "condition_b_naive": rate_dict(b_dmg_correct, b_dmg_incorrect, b_dmg_unverif),
            "fisher_exact_test": {
                "odds_ratio": fisher.odds_ratio,
                "p_two_sided": fisher.p_two_sided,
                "p_one_sided_a_greater_than_b": fisher.p_one_sided_greater,
                "significant_at_0.05": fisher.p_two_sided < 0.05,
            },
        },
        "secondary_context_all_claim_types": {
            "condition_a_grounded": rate_dict(*counts(a_claims)),
            "condition_b_naive": rate_dict(*counts(b_claims)),
        },
    }


def _load_checkpoint(out_path: Path) -> dict[str, FixtureResult]:
    """Fixture id -> already-completed FixtureResult, from a prior partial
    run at this same --out path (see main()'s --resume). Missing/unreadable
    file means "no checkpoint" (a fresh run), not an error."""
    if not out_path.exists():
        return {}
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        fixtures_raw = payload["report"]["fixtures"]
    except (json.JSONDecodeError, KeyError, OSError):
        return {}
    done: dict[str, FixtureResult] = {}
    for raw in fixtures_raw:
        try:
            done[raw["fixture_id"]] = FixtureResult.model_validate(raw)
        except Exception:  # noqa: BLE001 - a malformed row just isn't resumable
            continue
    return done


def _write_report(out_path: Path, provider_name: str, model: str, orchestrator: str,
                   results: list[FixtureResult]) -> dict[str, Any]:
    """Overwrite out_path with everything completed so far. Called after
    EVERY fixture (not just at the end) so a mid-run crash — e.g. a fixture
    that still 503s after exhausting _with_retry's budget — loses at most
    one fixture's real, paid-for API calls instead of the whole run's."""
    report = BenchmarkReport(provider=provider_name, model=model, orchestrator=orchestrator, fixtures=results)
    summary = _aggregate(report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": summary, "report": report.model_dump()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orchestrator", default="native", choices=["adk", "langchain", "native"],
        help="which AnalysisPipeline backend powers Condition A (default: native)",
    )
    parser.add_argument("--provider", default=None, help="openai|gemini (default: config default)")
    parser.add_argument("--out", default=None, help="output JSON path (default: out/report-<orchestrator>-<ts>.json)")
    parser.add_argument("--limit", type=int, default=None, help="only run the first N fixtures")
    parser.add_argument(
        "--trap-fixtures-only", action="store_true",
        help="use only the original 10 trap-category fixtures, not the 20 damage-dense ones",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="skip fixtures already completed in the file at --out (from a run that got cut short by a "
             "fixture exhausting its retry budget) and reuse their saved results instead of re-paying for them",
    )
    args = parser.parse_args()

    settings_container = Container()  # only to resolve provider/model names below; not used for any fixture
    provider_name = args.provider or settings_container.settings.default_provider
    model = (
        settings_container.settings.openai_model
        if provider_name == "openai"
        else settings_container.settings.gemini_model
    )
    settings_container.shutdown()
    fixtures = FIXTURES if args.trap_fixtures_only else ALL_FIXTURES
    if args.limit:
        fixtures = fixtures[: args.limit]

    out_path = Path(args.out) if args.out else _OUT_DIR / f"report-{args.orchestrator}-{int(time.time())}.json"
    checkpoint = _load_checkpoint(out_path) if args.resume else {}
    if checkpoint:
        print(f"[resume] reusing {len(checkpoint)} already-completed fixture(s) from {out_path}")

    def _run_one(f: Fixture) -> FixtureResult:
        # A fresh Container (hence a fresh Node calc subprocess / Firestore
        # client) per fixture, not one shared across the whole run: if a
        # fixture's wall-clock timeout fires below, its thread is abandoned
        # still running, and a shared Node/Firestore pipe would then be
        # written to concurrently by that orphaned thread and the next
        # fixture's — silently interleaving/corrupting IPC responses. A
        # per-fixture container costs a few seconds of subprocess spinup,
        # negligible next to the minutes an LLM call takes.
        c = Container()
        try:
            return _run_fixture(c, f, args.orchestrator, provider_name)
        finally:
            c.shutdown()

    results: list[FixtureResult] = []
    skipped: list[str] = []
    for f in fixtures:
        if f.id in checkpoint:
            results.append(checkpoint[f.id])
            print(f"=== {f.id} ({', '.join(f.tags)}) === [resumed from checkpoint]")
            continue
        # Not a `with` block: ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
        # which would block on the abandoned thread on timeout — exactly what
        # this is trying to avoid. shutdown(wait=False) below detaches it instead.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_run_one, f)
        try:
            results.append(future.result(timeout=_FIXTURE_WALL_CLOCK_TIMEOUT_SECONDS))
        except FutureTimeoutError:
            print(f"  [SKIPPED] {f.id} exceeded the {_FIXTURE_WALL_CLOCK_TIMEOUT_SECONDS:.0f}s "
                  "wall-clock budget (no exception raised — likely a long agent tool-calling loop, "
                  "not a single retriable call); abandoning it and moving on")
            skipped.append(f.id)
        except Exception as exc:  # noqa: BLE001 - transient SDK/network errors of many types
            print(f"  [SKIPPED] {f.id} still failing after {_MAX_ATTEMPTS} attempts: {exc}")
            skipped.append(f.id)
        finally:
            pool.shutdown(wait=False)
        # Checkpoint after every fixture (success or skip) so a later crash
        # never loses more than the one fixture in flight when it happens.
        _write_report(out_path, provider_name, model, args.orchestrator, results)

    if skipped:
        print(f"\n[!] {len(skipped)} fixture(s) skipped after exhausting retries: {', '.join(skipped)}")
        print(f"    Re-run with --resume --out {out_path} to fill them in once the provider recovers.")

    # results already includes every completed fixture (fresh + resumed);
    # the checkpoint write inside the loop above already saved this exact
    # state to out_path, so recompute the summary from it for the printed
    # report rather than writing the file a second time.
    summary = _write_report(out_path, provider_name, model, args.orchestrator, results)
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
    print(f"\n=== PRIMARY RESULT: damage_range, orchestrator={args.orchestrator} (n={len(results)} fixtures"
          f"{f', {len(skipped)} skipped' if skipped else ''}) ===")
    print(fisher_result.summary())
    print("\n=== secondary context: all claim types combined ===")
    ctx = summary["secondary_context_all_claim_types"]
    print(f"A: {ctx['condition_a_grounded']['correct']}/{ctx['condition_a_grounded']['total_claims']} "
          f"({ctx['condition_a_grounded']['strict_faithfulness_rate']:.1%})  vs  "
          f"B: {ctx['condition_b_naive']['correct']}/{ctx['condition_b_naive']['total_claims']} "
          f"({ctx['condition_b_naive']['strict_faithfulness_rate']:.1%})  "
          "-- mixes categories with and without an effect; not the headline, see README")
    print(f"\nFull report written to {out_path}")
    # Each fixture's own Container is shut down in _run_one's finally block
    # above; there's no run-wide shared one left to close here.


if __name__ == "__main__":
    main()
