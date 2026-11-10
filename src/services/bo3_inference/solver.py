"""Bounded joint search; partial searches never establish globally exact spreads."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from itertools import islice
from typing import Literal

from src.domain.bo3_inference.candidate_space import joint_assignments
from src.domain.bo3_inference.constraints import agrees
from src.domain.bo3_inference.dependencies import relevant_axes as relevant_axes
from src.domain.bo3_inference.evidence_index import SeriesEvidenceIndex
from src.domain.bo3_inference.models import (
    CandidateProfile,
    DamageObservation,
    DamageSupport,
    DeclaredSet,
    InferenceCheckpoint,
    InferenceOptions,
    ProjectionRequest,
    RuleProfile,
)
from src.domain.bo3_inference.session import retain_known_bounds, summarize
from src.domain.exceptions import CalcEngineError
from src.domain.interfaces import CandidateProjectionPort, ProgressReporter


class SearchLimit(Exception):
    """Internal control flow for a budgeted, explicitly incomplete search."""


class ProjectionBudget:
    def __init__(self, port: CandidateProjectionPort, options: InferenceOptions) -> None:
        self.port = port
        self.options = options
        self.deadline = time.monotonic() + options.timeout_seconds
        self.cache: OrderedDict[str, DamageSupport] = OrderedDict()
        self.projections = 0
        self.completed_checks = 0
        self.game_count = 1
        self.evidence_index: SeriesEvidenceIndex | None = None
        self.factor_limit_reasons: dict[int, set[str]] = {}
        self.factor_phase_starts: dict[int, int] = {}
        self.factor_deadlines: dict[int, float] = {}
        self.factor_checkpoints: dict[str, InferenceCheckpoint] = {}
        self._game_deadline: float | None = None
        self._game_projection_ceiling = options.max_projections
        self.progress: ProgressReporter | None = None
        self.progress_context = ""
        self._last_progress = float("-inf")

    def start_game(self, game: int) -> None:
        """Reserve time and projections for later games across every search route."""
        remaining_games = max(1, self.game_count - game + 1)
        now = time.monotonic()
        self._game_deadline = now + max(0.0, self.deadline - now) / remaining_games
        remaining = max(0, self.options.max_projections - self.projections)
        self._game_projection_ceiling = self.projections + max(1, remaining // remaining_games)
        self.factor_phase_starts[game] = self.projections
        self.factor_deadlines[game] = self._game_deadline

    def report_batch(self, requests: Sequence[ProjectionRequest]) -> None:
        """Report real work at most once a second, without exposing a guessed ETA."""
        if self.progress is None or not requests:
            return
        now = time.monotonic()
        if now - self._last_progress < 1:
            return
        self._last_progress = now
        request = requests[0].calc
        self.progress(f"{self.progress_context}: comparing {request.attacker.species}'s {request.move} "
                      f"against {request.defender.species}'s observed HP. "
                      f"Completed damage checks: {self.completed_checks:,} (series total).")

    def project(self, requests: Sequence[ProjectionRequest]) -> list[DamageSupport]:
        self.report_batch(requests)
        keys = [request.model_dump_json() for request in requests]
        resolved = {key: self.cache[key] for key in keys if key in self.cache}
        missing = {key: request for key, request in zip(keys, requests) if key not in self.cache}
        for key in keys:
            if key in self.cache:
                self.cache.move_to_end(key)
        if time.monotonic() >= self.deadline or self.projections + len(missing) > self.options.max_projections:
            raise SearchLimit("Projection/time budget reached")
        if ((self._game_deadline is not None and time.monotonic() >= self._game_deadline)
                or self.projections + len(missing) > self._game_projection_ceiling):
            raise SearchLimit("Per-game search budget reached; remaining work is reserved for later games")
        if missing:
            try:
                results = self.port.project_batch(list(missing.values()))
                if len(results) != len(missing):
                    raise CalcEngineError("Candidate response count mismatch")
            except CalcEngineError as exc:
                results = [DamageSupport(supported=False, reason=str(exc)) for _ in missing]
            self.projections += len(missing)
            self.completed_checks += sum(result.supported for result in results)
            resolved.update(zip(missing, results))
            self.cache.update(zip(missing, results))
            while len(self.cache) > self.options.max_cache_entries:
                self.cache.popitem(last=False)
        return [resolved[key] for key in keys]


def projection_request(observation: DamageObservation, assignment: Mapping[str, CandidateProfile]) -> ProjectionRequest:
    attacker = assignment[observation.attacker_id]
    defender = assignment[observation.defender_id]
    return ProjectionRequest(calc=observation.request, attacker_points=attacker.points, defender_points=defender.points,
                             attacker_nature=attacker.natures[0], defender_nature=defender.natures[0], before=observation.before,
                             attacker_before=observation.attacker_before)


def evaluate_batch(
    assignments: list[dict[str, CandidateProfile]], observations: Sequence[DamageObservation],
    budget: ProjectionBudget,
) -> tuple[list[int], set[int], set[str]]:
    alive = list(range(len(assignments)))
    unresolved: set[int] = set()
    reasons: set[str] = set()
    for observation in observations:
        results = budget.project([projection_request(observation, assignments[index]) for index in alive])
        survivors = []
        for index, support in zip(alive, results):
            if not support.supported:
                unresolved.add(index)
                reasons.add(f"{observation.id}: {support.reason}")
                survivors.append(index)
            elif agrees(observation, support):
                survivors.append(index)
        alive = survivors
        if not alive:
            break
    return alive, unresolved.intersection(alive), reasons


def enumerate_constraints(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    observations: Sequence[DamageObservation], unsupported: Sequence[str], rules: RuleProfile,
    budget: ProjectionBudget,
    assignment_limit: int | None = None,
) -> InferenceCheckpoint:
    axes = relevant_axes(observations)
    families: dict[str, dict[str, CandidateProfile]] = {key: {} for key in axes}
    examples: list[dict[str, CandidateProfile]] = []
    tested = compatible = unresolved = 0
    reasons = set(unsupported)
    partial = False
    iterator: Iterator[dict[str, CandidateProfile]] = joint_assignments(declarations, axes, rules) if axes else iter(())
    limit = assignment_limit if assignment_limit is not None else budget.options.max_assignments
    try:
        while tested < limit:
            batch = list(islice(iterator, min(64, limit - tested)))
            if not batch:
                break
            alive, uncertain, skipped = evaluate_batch(batch, observations, budget)
            reasons.update(skipped)
            tested += len(batch)
            unresolved += len(uncertain)
            for index in alive:
                if index in uncertain:
                    continue
                compatible += 1
                if len(examples) < 12:
                    examples.append(batch[index])
                for key, candidate in batch[index].items():
                    families[key][candidate.model_dump_json()] = candidate
        else:
            partial = next(iterator, None) is not None
    except SearchLimit as exc:
        partial = True
        reasons.add(str(exc))
    complete = not partial and not unresolved
    summaries = tuple(summarize(key, entry, rules, list(families.get(key, {}).values()) if complete and compatible else None)
                      for key, entry in declarations.items())
    coverage: Literal["exhaustive", "bounded", "unsupported"] = "exhaustive" if complete else "bounded"
    outcome: Literal["not_informative", "ambiguous", "identified_within_model", "conflict"] = "ambiguous"
    if not observations:
        outcome = "not_informative"
        coverage = "unsupported" if reasons else "exhaustive"
    elif complete and compatible == 0:
        outcome = "conflict"
    elif compatible == 0:
        outcome = "not_informative"
    elif complete and all(
        len(summary.natures) == 1 and all(r.minimum == r.maximum for r in summary.stat_points.values())
        for summary in summaries if summary.pokemon_id in axes
    ):
        outcome = "identified_within_model"
    return InferenceCheckpoint(id=f"g{game}:{phase}", game=game, phase=phase,
                               perspective=budget.options.perspective, outcome=outcome, coverage=coverage,
                               evaluated_assignments=tested, compatible_families=compatible if complete else None,
                               observation_ids=tuple(o.id for o in observations), unsupported=tuple(sorted(reasons)),
                               summaries=summaries, joint_examples=tuple(examples))


def solve(
    *, game: int, phase: Literal["before_game", "after_game"], declarations: Mapping[str, DeclaredSet],
    observations: Sequence[DamageObservation], unsupported: Sequence[str], rules: RuleProfile,
    budget: ProjectionBudget, assignment_limit: int | None = None,
) -> InferenceCheckpoint:
    """Use exact necessary local bounds when a full-series Cartesian search is huge."""
    from src.domain.bo3_inference.components import observation_components
    from src.services.bo3_inference.component_search import solve_components
    from src.services.bo3_inference.factor_bounds import local_bounds, space_bound

    limit = assignment_limit if assignment_limit is not None else budget.options.max_assignments
    components = observation_components(observations)
    factors = []
    if limit > 0 and observations and len(components) == 1 and space_bound(declarations, relevant_axes(observations), rules) > limit:
        factors = local_bounds(game=game, phase=phase, declarations=declarations, observations=observations,
                               rules=rules, budget=budget)
        # Do not spend the entire series budget exploring a huge Cartesian
        # product once sound local proofs exist. Preserve work for later games.
        if all(declarations[key].nature for key in relevant_axes(observations)):
            limit = min(limit, 256) if phase == "after_game" else 0
    if len(components) > 1:
        result = solve_components(game=game, phase=phase, declarations=declarations,
            components=components, unsupported=unsupported, rules=rules, budget=budget, assignment_limit=limit)
    else:
        result = enumerate_constraints(game=game, phase=phase, declarations=declarations,
            observations=observations, unsupported=unsupported, rules=rules, budget=budget, assignment_limit=limit)
    if budget.factor_limit_reasons.get(game):
        result = result.model_copy(update={"unsupported": tuple(dict.fromkeys(
            (*result.unsupported, *sorted(budget.factor_limit_reasons[game]))))})
    for proof in factors:
        if proof.outcome == "conflict":
            result = result.model_copy(update={"outcome": "conflict", "compatible_families": 0,
                                               "joint_examples": ()})
            break
        result = retain_known_bounds(result, proof)
    if factors:
        result = result.model_copy(update={
            "local_constraints": tuple(dict.fromkeys((*result.local_constraints, *(identifier for proof in factors
                if proof.coverage == "exhaustive" or proof.marginal_bounds_complete for identifier in proof.observation_ids)))),
            "partial_local_constraints": tuple(dict.fromkeys((*result.partial_local_constraints, *(identifier for proof in factors
                if proof.coverage != "exhaustive" and not proof.marginal_bounds_complete for identifier in proof.observation_ids)))),
            "local_evaluated_assignments": result.local_evaluated_assignments + sum(proof.evaluated_assignments for proof in factors),
        })
        if result.outcome == "not_informative" and any(proof.compatible_families or proof.marginal_bounds_complete
                or any(summary.basis == "necessary_local_bounds" for summary in proof.summaries) for proof in factors):
            result = result.model_copy(update={"outcome": "ambiguous"})
    return result
