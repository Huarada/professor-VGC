"""Evaluate replayable corpora with explicit denominators and no accuracy inflation."""

from __future__ import annotations

import hashlib
import platform
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.constraints import agrees
from src.domain.bo3_inference.models import CandidateProfile, InferenceCheckpoint
from src.domain.interfaces import CandidateProjectionPort
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import observations
from src.services.bo3_inference.solver import projection_request
from src.services.series_analysis import prepare_series

from .fixtures import Corpus, Fixture
from .independent import validate_oracle


def signature(checkpoint: InferenceCheckpoint) -> dict[str, Any]:
    """Compare semantic results, excluding event-side labels, timing and cache details."""
    return {"outcome": checkpoint.outcome, "coverage": checkpoint.coverage,
            "compatible_families": checkpoint.compatible_families,
            "summaries": [{"id": s.pokemon_id, "points": {k: v.model_dump() for k, v in s.stat_points.items()},
                           "natures": list(s.natures)} for s in checkpoint.summaries]}


def run_fixture(fixture: Fixture, port: CandidateProjectionPort) -> dict[str, Any]:
    request = Bo3AnalysisRequest(session_id="benchmark", games=list(fixture.games), teamsheet=fixture.teamsheet.model_dump(),
                                 inference_options=fixture.options)
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    started = time.perf_counter()
    result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=port).analyze(request, evidence, states)
    elapsed = time.perf_counter() - started
    assert result is not None
    checkpoints = result.checkpoints
    final = checkpoints[-1]
    summaries = {summary.pokemon_id: summary for summary in final.summaries}
    retained = all(key in summaries and value.nature in summaries[key].natures and all(
        summaries[key].stat_points[stat].minimum <= points <= summaries[key].stat_points[stat].maximum
        for stat, points in value.stat_points.items()) for key, value in fixture.truth.items())
    initial, maps = identities(evidence, states, fixture.teamsheet)
    assert all(value.nature is not None for value in fixture.truth.values())
    assignment = {key: CandidateProfile(pokemon_id=key, species=value.species, points=value.stat_points,
                                       natures=(value.nature or "Hardy",)) for key, value in fixture.truth.items()}
    truth_checks = []
    for number, (game, mapping) in enumerate(zip(states, maps), 1):
        declarations = visible_declarations(initial, fixture.teamsheet, number, fixture.options.perspective)
        found, _ = observations(game, number, declarations, mapping, port, fixture.options.hp_mode)
        for observation in found:
            support = port.project_batch([projection_request(observation, assignment)])[0]
            truth_checks.append(support.supported and agrees(observation, support))
    final_counts = [checkpoint.compatible_families for checkpoint in checkpoints
                    if checkpoint.phase == "after_game" and checkpoint.coverage == "exhaustive"
                    and checkpoint.compatible_families is not None]
    checks = {
        "expected_coverage": final.coverage == fixture.expected_coverage,
        "truth_in_reported_outer_ranges": retained,
        "truth_satisfies_all_modeled_damage": bool(truth_checks) and all(truth_checks),
        "all_damage_eligible": len(final.observation_ids) == len(fixture.games) and not final.unsupported
                               if fixture.expected_coverage == "exhaustive" else True,
        "no_false_unique_on_budget": final.coverage == "exhaustive" or final.outcome != "identified_within_model",
        "no_conflict": all(checkpoint.outcome != "conflict" for checkpoint in checkpoints),
        "progressive_family_count": all(a >= b for a, b in zip(final_counts, final_counts[1:])),
        "chronology": all(not any(obs.startswith(f"g{later}:") for obs in checkpoint.observation_ids)
                          for checkpoint in checkpoints for later in range(checkpoint.game + 1, len(states) + 1)),
        "projection_budget": result.projections <= fixture.options.max_projections,
    }
    first_width = sum(r.maximum - r.minimum for s in checkpoints[0].summaries for r in s.stat_points.values())
    last_width = sum(r.maximum - r.minimum for s in final.summaries for r in s.stat_points.values())
    return {"name": fixture.name, "provenance": fixture.provenance, "checks": checks, "passed": all(checks.values()),
            "latency_seconds": round(elapsed, 6), "production_projections": result.projections,
            "truth_probe_observations": len(truth_checks), "truth_outer_ranges_retained": retained,
            "modeled_damage_observations": len(final.observation_ids), "unsupported_reasons": list(final.unsupported),
            "final_outcome": final.outcome, "final_coverage": final.coverage,
            "compatible_families_after_games": final_counts,
            "marginal_width_reduction": (1 - last_width / first_width) if first_width else 0,
            "signature": signature(final)}


def run(corpus: Corpus, port: CandidateProjectionPort) -> dict[str, Any]:
    rules = port.rules()
    rows = []
    previous: dict[str, dict[str, Any]] = {}
    for fixture in corpus.fixtures:
        row = run_fixture(fixture, port)
        if fixture.equivalent_to:
            row["checks"]["side_and_nickname_invariance"] = row["signature"] == previous[fixture.equivalent_to]
        row["passed"] = all(row["checks"].values())
        previous[fixture.name] = row.pop("signature")
        rows.append(row)
    oracle = validate_oracle()
    latencies = sorted(row["latency_seconds"] for row in rows)
    passed = all(row["passed"] for row in rows + oracle)
    return {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "corpus_sha256": hashlib.sha256(corpus.model_dump_json().encode()).hexdigest(),
        "engine_version": rules.engine_version, "rule_version": rules.version,
        "fixture_engine_version": corpus.generator_engine_version,
        "fixture_rule_version": corpus.rule_version,
        "passed": passed,
        "summary": {"fixtures": len(rows), "fixtures_passed": sum(row["passed"] for row in rows),
                    "games": sum(len(f.games) for f in corpus.fixtures),
                    "independent_oracle_cases": len(oracle), "independent_oracle_passed": sum(r["passed"] for r in oracle),
                    "truth_outer_ranges_retained": sum(row["truth_outer_ranges_retained"] for row in rows),
                    "production_projections": sum(row["production_projections"] for row in rows),
                    "median_latency_seconds": statistics.median(latencies),
                    "max_latency_seconds": max(latencies),
                    "independently_recorded_fixtures": sum(f.provenance == "independently_recorded" for f in corpus.fixtures)},
        "limitations": [
            "Engine-generated fixtures test self-consistency, not held-out real-game accuracy.",
            "Analytic oracle validates solver relationships, not Champions damage mechanics.",
            "Marginal truth retention is not a proof that every combination inside ranges is compatible.",
            "Truth probes independently check constraints but do not inspect the solver's full joint relation.",
            "Marginal width reduction is not candidate-volume reduction or a calibrated probability.",
            "Latency is observational; no portable wall-clock performance threshold is claimed.",
            "Truth-probe and fixture-generation projections are excluded from production-projection counts.",
        ], "fixtures": rows, "independent_oracle": oracle,
    }
