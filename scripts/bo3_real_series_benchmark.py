"""Offline, held-out damage validation of the recorded testProfessor/Huarada BO3.

Known spreads score calculator outputs; they never enter public inference. The
conditional Floette sweep deliberately knows the other attributes and therefore
must not be presented as public inference or complete timeline reconstruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
from src.domain.bo3_inference.models import (
    CandidateProfile,
    DamageObservation,
    DamageSupport,
    DeclaredSet,
    HPReading,
    InferenceOptions,
    TeamSheet,
)
from src.domain.models import GameState
from src.domain.series import Bo3AnalysisRequest, Bo3Evidence
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.bo3_inference.declarations import identities, visible_declarations
from src.services.bo3_inference.observations import observations
from src.services.bo3_inference.solver import projection_request
from src.services.series_analysis import prepare_series

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "series"
PUBLIC = FIXTURES / "bo3-public-2709311277.json"
TRUTH = FIXTURES / "bo3-truth-2709311277.json"
FLOETTE = "Huarada:slot3"
SeriesData = tuple[Bo3AnalysisRequest, Bo3Evidence, list[GameState], TeamSheet,
                   dict[str, DeclaredSet], list[DamageObservation], list[str]]


def recorded_games() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(PUBLIC.read_text(encoding="utf-8"))["games"])


def collect(adapter: SmogonCandidateProjectionAdapter,
            games: Sequence[dict[str, Any] | str] | None = None) -> SeriesData:
    """Extract public observations before opening the separate truth file."""
    request = Bo3AnalysisRequest(session_id="real-series-testbench",
        games=list(games) if games is not None else list(recorded_games()))
    evidence, states = prepare_series(ShowdownReplayParser(), request)
    sheet = ShowdownTeamSheetParser().parse(request.games)
    initial, mappings = identities(evidence, states, sheet)
    hits: list[DamageObservation] = []
    exclusions: list[str] = []
    for game, (state, mapping) in enumerate(zip(states, mappings), 1):
        found, skipped = observations(state, game, visible_declarations(initial, sheet, game, "public"),
                                      mapping, adapter, "percent")
        hits.extend(found)
        exclusions.extend(skipped)
    return request, evidence, states, sheet, initial, hits, exclusions


def held_out_profiles(initial: Mapping[str, DeclaredSet]) -> dict[str, CandidateProfile]:
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))["teams"]
    profiles = {}
    for key, entry in initial.items():
        known = truth[entry.participant][entry.species]
        points = known["points"]
        if sum(points.values()) != 66 or any(not 0 <= value <= 32 for value in points.values()):
            raise ValueError(f"Invalid held-out native spread: {key}")
        profiles[key] = CandidateProfile(pokemon_id=key, species=entry.species,
            points=points, natures=(known["nature"],))
    return profiles


def matching_witnesses(hit: DamageObservation, support: DamageSupport) -> list[dict[str, int]]:
    """Independent before/after HP oracle; do not call production agrees/displays."""
    if not support.supported:
        return []
    result = []
    for witness in support.witnesses:
        if witness.damage < 0 or not matches_hp(witness.hp_before, support.maximum_hp, hit.before):
            continue
        remaining = max(0, witness.hp_before - witness.damage)
        if matches_hp(remaining, support.maximum_hp, hit.after):
            result.append({"hp_before": witness.hp_before, "damage": witness.damage, "hp_after": remaining})
    return result


def matches_hp(hp: int, maximum: int, reading: HPReading) -> bool:
    """Evaluate integer HP with an oracle separate from inference constraints."""
    if maximum <= 0 or not 0 <= hp <= maximum:
        return False
    if reading.mode == "exact":
        return reading.denominator == maximum and reading.numerator == hp
    displayed = max(1, reading.denominator * hp // maximum) if hp else 0
    if displayed != reading.numerator:
        return False
    if reading.color and displayed == 20:
        return (hp * 5 > maximum) == (reading.color == "y")
    if reading.color and displayed == 50:
        return (hp * 2 > maximum) == (reading.color == "g")
    return True


def conditional_spa_sweep(adapter: SmogonCandidateProjectionAdapter, hits: Sequence[DamageObservation],
                          profiles: Mapping[str, CandidateProfile]) -> list[dict[str, Any]]:
    """Keep every other known value fixed, intersecting evidence in game order."""
    checkpoints = []
    remaining = set(range(33))
    uncertain: set[int] = set()
    for game in (1, 2, 3):
        current = [hit for hit in hits if hit.game == game and hit.attacker_id == FLOETTE]
        rejected_by = {}
        unresolved_by = {}
        for spa in sorted(remaining):
            original = profiles[FLOETTE]
            candidate = {**profiles, FLOETTE: original.model_copy(update={"points": {**original.points, "spa": spa}})}
            support = adapter.project_batch([projection_request(hit, candidate) for hit in current])
            rejected = [hit.id for hit, result in zip(current, support)
                        if result.supported and not matching_witnesses(hit, result)]
            if rejected:
                rejected_by[str(spa)] = rejected
            elif any(not result.supported for result in support):
                uncertain.add(spa)
                unresolved_by[str(spa)] = [hit.id for hit, result in zip(current, support) if not result.supported]
        remaining -= {int(value) for value in rejected_by}
        uncertain &= remaining
        checkpoints.append({"game": game, "new_observation_ids": [hit.id for hit in current],
            "compatible_spa": sorted(remaining - uncertain), "not_ruled_out_spa": sorted(remaining),
            "unresolved_spa": sorted(uncertain), "rejected_by": rejected_by, "unresolved_by": unresolved_by})
    return checkpoints


def report(adapter: SmogonCandidateProjectionAdapter) -> dict[str, Any]:
    request, evidence, states, sheet, initial, hits, exclusions = collect(adapter)
    if request.teamsheet is not None or any(entry.stat_points for entry in sheet.pokemon):
        raise ValueError("Public input unexpectedly exposes hidden native points")
    # Run the public search BEFORE loading reference spreads, under a fixed
    # work cap. Wall-clock exhaustion is still explicit in checkpoint coverage.
    options = InferenceOptions(max_projections=12000, max_assignments=12000)
    public_result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(),
        public_sheets=ShowdownTeamSheetParser(), projection=adapter).analyze(
            request.model_copy(update={"inference_options": options}), evidence, states)
    if public_result is None:
        raise ValueError("Public inference did not produce checkpoints")
    profiles = held_out_profiles(initial)
    checkpoints = []
    for checkpoint in public_result.checkpoints:
        if checkpoint.phase != "after_game":
            continue
        retained = all(bound.minimum <= profiles[summary.pokemon_id].points[stat] <= bound.maximum
                       for summary in checkpoint.summaries for stat, bound in summary.stat_points.items())
        floette = next(summary for summary in checkpoint.summaries if summary.pokemon_id == FLOETTE)
        checkpoints.append({"game": checkpoint.game, "outcome": checkpoint.outcome,
            "coverage": checkpoint.coverage, "observations": len(checkpoint.observation_ids),
            "certified_constraints": len(checkpoint.local_constraints),
            "truth_retained": retained, "floette_public_bounds": {
                stat: bound.model_dump() for stat, bound in floette.stat_points.items()},
            "narrowed": {summary.pokemon_id: {
                stat: bound.model_dump() for stat, bound in summary.stat_points.items()
                if (bound.minimum, bound.maximum) != (0, 32)}
                for summary in checkpoint.summaries
                if any((bound.minimum, bound.maximum) != (0, 32) for bound in summary.stat_points.values())},
            "limitations": list(checkpoint.unsupported)})
    projected = adapter.project_batch([projection_request(hit, profiles) for hit in hits])
    rows = []
    for hit, support in zip(hits, projected):
        witnesses = matching_witnesses(hit, support)
        rows.append({"id": hit.id, "game": hit.game, "attacker": hit.attacker_id,
            "defender": hit.defender_id, "move": hit.request.move,
            "before": hit.before.model_dump(), "after": hit.after.model_dump(),
            "maximum_hp": support.maximum_hp, "censored_ko": hit.after.numerator == 0,
            "supported": support.supported, "reason": support.reason,
            "matching_witnesses": witnesses, "passed": bool(witnesses)})
    sweep = conditional_spa_sweep(adapter, hits, profiles)
    counts = [sum(hit.game == game for hit in hits) for game in (1, 2, 3)]
    checks = {
        "recorded_coverage": counts == [10, 18, 16] and len({hit.id for hit in hits}) == 44,
        "floette_coverage": sum(FLOETTE in (hit.attacker_id, hit.defender_id) for hit in hits) == 15,
        "no_exclusions": not exclusions,
        "all_damage_matches": bool(rows) and all(row["passed"] for row in rows),
        "complete_checkpoints": [checkpoint["game"] for checkpoint in checkpoints] == [1, 2, 3]
            and [checkpoint["observations"] for checkpoint in checkpoints] == [10, 28, 44],
        "truth_retained": bool(checkpoints) and all(checkpoint["truth_retained"] for checkpoint in checkpoints),
        "no_public_conflict": all(checkpoint["outcome"] != "conflict" for checkpoint in checkpoints),
        "certified_narrowing": any(checkpoint["certified_constraints"] and checkpoint["narrowed"]
                                   for checkpoint in checkpoints),
        "projection_budget": public_result.projections <= options.max_projections,
        "declared_natures": len({(entry.participant, entry.slot) for entry in sheet.pokemon}) == len(profiles) == 12 and all(
            entry.nature == profiles[f"{entry.participant}:slot{entry.slot}"].natures[0] for entry in sheet.pokemon),
        "reference_spa_retained": len(sweep) == 3 and all(32 in checkpoint["compatible_spa"] for checkpoint in sweep),
        "recorded_series_result": evidence.complete and evidence.score == {"Huarada": 2, "testProfessor": 1},
    }
    return {"schema_version": 2, "passed": all(checks.values()), "checks": checks,
        "provenance": {"public_sha256": hashlib.sha256(PUBLIC.read_bytes()).hexdigest(),
            "truth_sha256": hashlib.sha256(TRUTH.read_bytes()).hexdigest(),
            "replays": [game["id"] for game in recorded_games()],
            "engine": adapter.rules().model_dump(mode="json")},
        "summary": {"games": len(evidence.games), "observations": len(hits),
            "observations_per_game": counts,
            "passed_observations": sum(bool(row["passed"]) for row in rows),
            "floette_observations": sum(FLOETTE in (hit.attacker_id, hit.defender_id) for hit in hits),
            "exclusions": exclusions},
        "damage": rows, "conditional_floette_spa": sweep,
        "blind_public_search": {"options": options.model_dump(mode="json"),
            "projections": public_result.projections, "checkpoints": checkpoints},
        "limitations": ["Known spreads score damage; they are not inputs to public inference.",
            "The conditional sweep fixes every other supplied value, including target spreads.",
            "Matching local HP witnesses does not prove one globally consistent HP timeline.",
            "A KO censors damage; matching it does not identify the actual random roll.",
            "One recorded BO3 cannot establish general inference accuracy."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    adapter = SmogonCandidateProjectionAdapter(ipc=NodeIpcClient(
        server_script=ROOT / "node_calc" / "calc_server.js", timeout_seconds=15))
    try:
        result = report(adapter)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": result["passed"], **result["summary"]}, indent=2))
        return 0 if result["passed"] else 1
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
