"""Best-of-three coaching without changing the BO1 pipeline or battle state."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from src.domain.champions import forbidden_references, require_champions_format
from src.domain.bo3_inference.narration import contains_numerical_investment
from src.domain.exceptions import (
    LLMProviderError,
    SeriesValidationError,
    UnsupportedRulesError,
)
from src.domain.interfaces import (
    AnalysisPipeline,
    ConversationMemory,
    LLMProvider,
    LogParser,
    PromptRepository,
    RegulationCatalog,
)
from src.domain.models import AnalysisRequest, ChatMessage, GameState
from src.domain.regulation import Regulation, RegulationScope, normalize_species
from src.domain.series import (
    Bo3AnalysisRequest,
    Bo3AnalysisResult,
    Bo3Evidence,
    SeriesGameEvidence,
)
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.ground_truth import explain_champions_only, parse_replay
from src.services.regulation_guard import RegulationGuard


def _identity(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _revealed(state: GameState, names: dict[str, str]) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Only log-revealed facts; no Chaos guesses or end-of-game state carryover."""
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for side in state.sides:
        result[names[side.player]] = {
            mon.species: {
                "moves": list(mon.moves),
                "abilities": [mon.ability] if mon.ability else [],
                "items": [mon.item] if mon.item else [],
                "formes": list(mon.battle_formes),
            }
            for mon in side.team if mon.species in side.brought()
        }
    # Consumed items can be absent from the final set but visible in snapshots.
    for event in state.outcome.events if state.outcome else []:
        if event.state is None:
            continue
        for side in state.sides:
            for species, facts in result[names[side.player]].items():
                mon = event.state.mon(side.player, species)
                if mon.item and mon.item not in facts["items"]:
                    facts["items"].append(mon.item)
    return result


def prepare_series(
    parser: LogParser, request: Bo3AnalysisRequest, pinned: Regulation | None = None,
) -> tuple[Bo3Evidence, list[GameState]]:
    """Validate every game before any paid call; preserve chronological knowledge."""
    if forbidden_references(request.question):
        raise UnsupportedRulesError("Only Pokemon Champions coaching with Mega Evolution is supported.")
    states = [parse_replay(parser, AnalysisRequest(session_id=request.session_id,
              replay_json=game if isinstance(game, dict) else None,
              replay_raw_text=game if isinstance(game, str) else None)) for game in request.games]
    players: list[str] = []
    identities: dict[str, str] = {}
    score: dict[str, int] = {}
    games: list[SeriesGameEvidence] = []
    regulation: Regulation | None = None
    fingerprints: set[str] = set()
    previews: dict[str, set[str]] = {}
    limitations: list[str] = []
    for number, (source, state) in enumerate(zip(request.games, states), 1):
        require_champions_format(state.format_id)
        current = RegulationScope(pinned).bind(state.format_id)
        if current is None or (regulation is not None and current != regulation):
            raise SeriesValidationError("Every BO3 replay must identify the same Champions regulation.")
        regulation = current
        if any(wins >= 2 for wins in score.values()):
            raise SeriesValidationError("A BO3 ends at two wins; a later game cannot belong to this series.")
        fingerprint = (str(source["id"]) if isinstance(source, dict) and source.get("id")
                       else json.dumps(state.model_dump(mode="json"), sort_keys=True))
        if fingerprint in fingerprints:
            raise SeriesValidationError("The same game was supplied more than once.")
        fingerprints.add(fingerprint)
        if len(state.sides) != 2 or {s.player for s in state.sides} != {"p1", "p2"}:
            raise SeriesValidationError("Each BO3 game must contain p1 and p2.")
        keys = [_identity(s.player_name) for s in state.sides]
        if not all(keys) or len(set(keys)) != 2:
            raise SeriesValidationError("BO3 needs two distinct player names to follow players across games.")
        if not players:
            players = [s.player_name for s in state.sides]
            identities = dict(zip(keys, players))
            score = dict.fromkeys(players, 0)
        if set(keys) != set(identities):
            raise SeriesValidationError("The same two players must appear in every BO3 game.")
        names = {s.player: identities[_identity(s.player_name)] for s in state.sides}
        before = dict(score)
        outcome = state.outcome
        if outcome is None or not outcome.events:
            raise SeriesValidationError("BO3 requires battle logs, not team-only JSON.")
        if outcome.winner_player is None and number < len(states):
            raise SeriesValidationError("Only the last supplied game may have an unknown result.")
        winner = names.get(outcome.winner_player or "")
        if winner:
            score[winner] += 1
        else:
            limitations.append(f"Game {number} has no recorded winner; its result remains unknown.")
        brought = {names[s.player]: list(s.brought()) for s in state.sides}
        if any(len(s.team) > 6 or len(s.brought()) > 4 for s in state.sides):
            raise SeriesValidationError("Champions teams have at most six Pokemon and bring at most four.")
        # Compare declared previews only; different bring-four choices are legal.
        raw = source if isinstance(source, str) else str(source.get("log", ""))
        for side in state.sides:
            if f"|poke|{side.player}|" in raw:
                team = {normalize_species(mon.species) for mon in side.team}
                name = names[side.player]
                if name in previews and previews[name] != team:
                    raise SeriesValidationError("A player's declared team changed between BO3 games.")
                previews[name] = team
        leads: dict[str, list[str]] = {name: [] for name in players}
        for event in outcome.events:
            if event.kind == "move":
                break
            if event.kind == "switch" and event.actor_player in names and event.actor:
                lead = leads[names[event.actor_player]]
                if event.actor not in lead and len(lead) < 2:
                    lead.append(event.actor)
        changes = [name for name in players if games and set(leads[name]) != set(games[-1].leads[name])]
        games.append(SeriesGameEvidence(
            number=number, winner=winner, score_before=before, score_after=dict(score),
            players_by_side=names, leads=leads, brought=brought, revealed=_revealed(state, names),
            known_before=[{"game": g.number, "revealed": g.revealed} for g in games],
            lead_changes=changes,
        ))
    assert regulation is not None  # Request validation requires at least one game.
    series_winner = next((name for name, wins in score.items() if wins == 2), None)
    if not series_winner:
        limitations.append("Incomplete series: no series winner can be asserted.")
    return Bo3Evidence(format_id=regulation.format_id, players=players, games=games,
                       score=score, complete=series_winner is not None, winner=series_winner,
                       limitations=limitations), states


class Bo3AnalysisService:
    """Review isolated games through any backend, then synthesize typed evidence."""

    def __init__(self, *, parser: LogParser, pipeline_factory: Callable[[], AnalysisPipeline],
                 llm: LLMProvider, prompts: PromptRepository, pinned: Regulation | None = None,
                 regulation_catalog: RegulationCatalog | None = None,
                 guard: RegulationGuard | None = None,
                 memory: ConversationMemory | None = None,
                 inference: Bo3InferenceCoordinator | None = None) -> None:
        self._parser = parser
        self._pipeline_factory = pipeline_factory
        self._llm = llm
        self._system = prompts.get("series_system")
        self._pinned = pinned
        self._catalog = regulation_catalog
        self._guard = guard or RegulationGuard(None)
        self._memory = memory
        self._inference = inference

    def analyze(self, request: Bo3AnalysisRequest) -> Bo3AnalysisResult:
        evidence, states = prepare_series(self._parser, request, self._pinned)
        inferred = self._inference.analyze(request, evidence, states) if self._inference else None
        run_id = uuid.uuid4().hex
        results = []
        for source, game in zip(request.games, evidence.games):
            game_request = AnalysisRequest(
                session_id=f"{request.session_id}:bo3:{run_id}:{game.number}",
                replay_json=source if isinstance(source, dict) else None,
                replay_raw_text=source if isinstance(source, str) else None,
                question=request.question, provider=request.provider,
                series_context={"game": game.number, "players_by_side": game.players_by_side,
                                "score_before": game.score_before, "known_before": game.known_before,
                                "set_inference": {
                                    "perspective": inferred.perspective,
                                    "checkpoints": [checkpoint.model_dump(mode="json") for checkpoint in inferred.checkpoints
                                                    if checkpoint.game == game.number],
                                } if inferred else None},
            )
            try:
                results.append(self._pipeline_factory().analyze(game_request))
            finally:
                if self._memory is not None:
                    self._memory.clear(game_request.session_id)
        payload = {
            "question": request.question, "series": evidence.model_dump(mode="json"),
            "set_inference": inferred.model_dump(mode="json") if inferred else None,
            "game_evidence": [result.model_dump(mode="json", exclude={
                "answer", "session_id", "question", "agent_tool_calls",
            }) for result in results],
        }

        def explain(correction: str) -> str:
            for _ in range(2):
                answer = self._llm.complete(
                    system=self._system, messages=[ChatMessage(role="user", content=(
                        json.dumps(payload, ensure_ascii=False) + "\n" + correction
                    ))], temperature=0.2,
                )
                if not answer.strip():
                    raise LLMProviderError("The BO3 explanation was empty.")
                if inferred is None or not contains_numerical_investment(answer):
                    return answer
                correction += ("\nRemove numerical investment allocations, baseline builds and legacy EV conversions. "
                               "The deterministic stat-point tables report all numerical investment. "
                               "Explain uncertainty and battle adjustments in prose instead.")
            raise LLMProviderError("The BO3 summary repeatedly included numerical investment prose. Please retry the analysis.")

        answer = explain_champions_only(explain)
        if self._catalog is not None:
            roster = self._catalog.roster(evidence.format_id)
            violations = self._guard.violations(answer, roster)
            if violations:
                answer = explain_champions_only(
                    explain, self._guard.correction_note(violations, evidence.format_id),
                )
                if self._guard.violations(answer, roster):
                    raise SeriesValidationError("The series summary included Pokemon outside its regulation.")
        return Bo3AnalysisResult(session_id=request.session_id,
                                 answer=answer, evidence=evidence, games=results, inference=inferred)
