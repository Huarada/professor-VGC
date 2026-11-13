"""BO3-only inference coordination, validation and decision-time checkpoints."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from src.domain.bo3_inference.citations import cite_observation
from src.domain.bo3_inference.dependencies import relevant_axes
from src.domain.bo3_inference.evidence_index import SeriesEvidenceIndex
from src.domain.bo3_inference.models import (
    Bo3InferenceEvidence,
    DamageCitation,
    DamageObservation,
    EvidenceQuery,
    EvidenceRetrieval,
    TeamSheet,
)
from src.domain.bo3_inference.session import SeriesInferenceSession, retain_known_bounds
from src.domain.exceptions import CalcEngineError, SeriesValidationError
from src.domain.interfaces import (
    AttributeProjectionPort,
    CandidateProjectionPort,
    ProgressReporter,
    ReplayTeamSheetParser,
    TeamSheetParser,
)
from src.domain.models import GameState
from src.domain.names import to_id
from src.domain.series import Bo3AnalysisRequest, Bo3Evidence
from src.services.bo3_inference.attributes import AttributeProjectionService
from src.services.bo3_inference.declarations import (
    identities,
    merge_sheets,
    person,
    visible_declarations,
    with_reveals,
)
from src.services.bo3_inference.observations import observations
from src.services.bo3_inference.projection_service import Bo3ProjectionService
from src.services.bo3_inference.solver import ProjectionBudget, solve


class Bo3InferenceCoordinator:
    """A bounded deterministic stage, shared by all BO3 explanation backends."""

    def __init__(self, *, sheets: TeamSheetParser, projection: CandidateProjectionPort,
                 public_sheets: ReplayTeamSheetParser | None = None,
                 attributes: AttributeProjectionPort | None = None,
                 progress: ProgressReporter | None = None) -> None:
        self._sheets = sheets
        self._projection = projection
        self._public_sheets = public_sheets
        self._attributes = attributes
        self._progress = progress

    def _report(self, message: str) -> None:
        if self._progress is not None:
            self._progress(message)

    def validate(self, request: Bo3AnalysisRequest, evidence: Bo3Evidence, games: Sequence[GameState]) -> TeamSheet:
        sheet = self._sheets.parse(request.teamsheet)
        if self._public_sheets:
            sheet = merge_sheets(sheet, self._public_sheets.parse(request.games))
        initial, maps = identities(evidence, games, sheet)
        perspective = request.inference_options.perspective
        if perspective not in {"public", "analyst"} and person(perspective) not in {person(name) for name in evidence.players}:
            raise SeriesValidationError("Inference perspective must be public, analyst or a series player's name.")
        traits: dict[str, dict[str, tuple[str, int]]] = {}
        for number, (game, mapping) in enumerate(zip(games, maps), 1):
            self._report(f"Stage 1 of 3 — Game {number} of {len(games)}: checking declared natures, items and abilities...")
            if not sheet.registered_team:
                traits = {}
            current = visible_declarations(initial, sheet, number, perspective)
            self._remember_initial_traits(game, mapping, traits, number + 1)
            with_reveals(current, traits)  # Validation only; no future reveals enter a checkpoint.
        return sheet

    def analyze(
        self, request: Bo3AnalysisRequest, evidence: Bo3Evidence, games: Sequence[GameState],
    ) -> Bo3InferenceEvidence | None:
        if not request.inference_options.enabled:
            return None
        sheet = self.validate(request, evidence, games)
        try:
            rules = self._projection.rules()
        except CalcEngineError as exc:
            return Bo3InferenceEvidence(continuity="registered_team" if sheet.registered_team else "per_game",
                                         limitations=(f"Champions inference engine unavailable: {exc}",))
        known_natures = {nature.name.casefold() for nature in rules.natures}
        if any(entry.nature and entry.nature.casefold() not in known_natures for entry in sheet.pokemon):
            raise SeriesValidationError("The teamsheet contains an unrecognized nature.")
        initial, maps = identities(evidence, games, sheet)
        session = SeriesInferenceSession(rules=rules)
        budget = ProjectionBudget(self._projection, request.inference_options)
        budget.progress = self._progress
        budget.game_count = len(games)
        index = SeriesEvidenceIndex(scope=uuid.uuid4().hex, pokemon_ids=initial)
        budget.evidence_index = index
        retrievals: list[EvidenceRetrieval] = []
        prior: list[DamageObservation] = []
        excluded: list[str] = []
        prior_traits: dict[str, dict[str, tuple[str, int]]] = {}
        projected = []
        citations: list[DamageCitation] = []
        attribute_service = AttributeProjectionService(engine=self._attributes) if self._attributes else None
        known_forms: dict[str, set[str]] = {}
        for number, (game, mapping) in enumerate(zip(games, maps), 1):
            budget.start_game(number)
            scope = f"Stage 1 of 3 — Game {number} of {len(games)}"
            budget.progress_context = scope
            current = visible_declarations(initial, sheet, number, request.inference_options.perspective)
            if not sheet.registered_team:
                retrievals.extend(index.retrievals)
                index = SeriesEvidenceIndex(scope=uuid.uuid4().hex, pokemon_ids=initial)
                budget.evidence_index = index
                prior, excluded = [], []
                known_forms = {}
            else:
                current = with_reveals(current, prior_traits)
            self._report(f"{scope}: carrying forward compatible stat ranges from earlier games..."
                         if prior else f"{scope}: establishing initial stat ranges from the teamsheet...")
            before = solve(game=number, phase="before_game", declarations=current,
                           observations=prior, unsupported=excluded, rules=rules, budget=budget)
            if sheet.registered_team and session.checkpoints:
                before = retain_known_bounds(before, session.checkpoints[-1])
            if attribute_service:
                before = attribute_service.enrich(before, known_forms)
            session.append(before)
            self._report(f"{scope}: reconstructing HP, items, abilities and field effects at each attack...")
            try:
                found, skipped = observations(game, number, current, mapping, self._projection,
                                              request.inference_options.hp_mode)
            except CalcEngineError as exc:
                found, skipped = [], [f"g{number}: engine metadata unavailable: {exc}"]
            index.append(found)
            citations.extend(cite_observation(hit, relevant_axes([hit])) for hit in found)
            prior = list(index.query(EvidenceQuery(through_game=number)))
            excluded.extend(skipped)
            self._report(f"{scope}: matching {len(found)} eligible hits against legal stat-point spreads; "
                         f"{len(prior)} hits available across the series...")
            checkpoint = solve(game=number, phase="after_game", declarations=current,
                               observations=prior, unsupported=excluded, rules=rules, budget=budget)
            checkpoint = retain_known_bounds(checkpoint, before)
            if checkpoint.outcome == "conflict":
                checkpoint = checkpoint.model_copy(update={"summaries": session.last_valid(current)})
            if attribute_service:
                self._report(f"{scope}: calculating HP, Atk, Def, SpA, SpD and Spe ranges for base and observed Mega forms...")
                for side in game.sides:
                    for mon in side.team:
                        known_forms.setdefault(mapping[(side.player, mon.species)], set()).update(
                            form for form in mon.battle_formes if re.search(r"-mega(?:-[xyz])?$", form, re.IGNORECASE)
                        )
                checkpoint = attribute_service.enrich(checkpoint, known_forms)
            session.append(checkpoint)
            self._report(f"{scope}: checking damage ranges against the compatible spread examples...")
            projected.extend(Bo3ProjectionService.describe(checkpoint, prior, budget))
            coverage_note = {"exhaustive": "all candidate spreads checked",
                             "bounded": "some spreads remain unresolved",
                             "unsupported": "not enough supported damage evidence"}[checkpoint.coverage]
            self._report(f"{scope}: damage validation finished — {coverage_note}. "
                         f"{len(checkpoint.local_constraints)} fully checked damage comparisons.")
            if sheet.registered_team:
                self._remember_initial_traits(game, mapping, prior_traits, number + 1)
        limitations = ["IVs are fixed at 31; results infer stat points and nature, never IVs.",
                       "Only modeled direct damage is used; attributed critical hits and stable-state logged multi-hit counts are supported. Speed inference is excluded.",
                       "Joint examples are profile families with unmeasured legal completions, not exact complete spreads.",
                       "Attribute tables show unboosted level-50 outer ranges, not logged or item-modified battle stats."]
        if not sheet.registered_team:
            limitations.append("Registered-team continuity was not declared; candidate constraints reset each game.")
        return Bo3InferenceEvidence(rule_version=rules.version, engine_version=rules.engine_version,
                                     perspective=request.inference_options.perspective,
                                     continuity="registered_team" if sheet.registered_team else "per_game",
                                     checkpoints=tuple(session.checkpoints), limitations=tuple(limitations),
                                     projections=budget.projections, conditioned_projections=tuple(projected),
                                     attribute_projections=attribute_service.projections if attribute_service else 0,
                                     evidence_retrievals=tuple(retrievals) + index.retrievals,
                                     damage_citations=tuple(citations))

    @staticmethod
    def _remember_initial_traits(
        game: GameState, mapping: dict[tuple[str, str], str], traits: dict[str, dict[str, tuple[str, int]]],
        available: int,
    ) -> None:
        for side in game.sides:
            for mon in side.team:
                record = traits.setdefault(mapping[(side.player, mon.species)], {})
                values = {"item": mon.item}
                # Never promote a copied/changed ability to a permanent series fact.
                if mon.ability and not mon.battle_formes and not game.inference_exclusions:
                    values["ability"] = mon.ability
                for field, value in values.items():
                    if value is not None:
                        if field in record and to_id(record[field][0]) != to_id(value):
                            raise SeriesValidationError(f"A registered initial {field} changed across games.")
                        record.setdefault(field, (value, available))
