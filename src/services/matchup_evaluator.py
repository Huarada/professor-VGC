"""Deterministic matchup evaluation — shared across orchestration backends.

Wraps the deterministic damage-calc + speed-tier logic so that BOTH the native
:class:`~src.services.analysis_service.AnalysisService` and the LangChain
orchestrator run identical, ground-truth calculations.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.domain.exceptions import CalcEngineError, StrategyKnowledgeError
from src.domain.interfaces import CalcEngineAdapter, StrategyKnowledgeProvider
from src.domain.models import (
    CalcRequest,
    DamageResult,
    GameState,
    MatchupVerdict,
    MetaContext,
    PokemonSet,
    SelectionPlan,
    SmogonStrategy,
    SpeedComparison,
)

_DEFAULT_MOVE = "Tackle"


class MatchupEvaluator:
    """Runs deterministic calcs for a selection plan against a game state."""

    def __init__(self, calc_engine: CalcEngineAdapter, default_gen: int = 9) -> None:
        self._calc = calc_engine
        self._gen = default_gen
        self._forme_resolve_cache: dict[tuple[int, str], bool] = {}

    def index_sets(self, game_state: GameState) -> dict[str, PokemonSet]:
        """Map species -> best-known set from the battle state."""
        index: dict[str, PokemonSet] = {}
        for side in game_state.sides:
            for mon in side.team:
                index.setdefault(mon.species, mon)
        return index

    def enrich_set(
        self,
        mon: PokemonSet,
        meta: MetaContext,
        statuses: dict[str, str] | None = None,
        boosts: dict[str, int] | None = None,
    ) -> PokemonSet:
        """Back-fill hidden ability/item/nature/EVs (Chaos) and observed status
        (log). The nature/EVs back-fill uses the single most-used competitive
        spread for this species in the ideal tier (meta.pokemon_stats) instead
        of leaving the calc to silently default to 0 EVs/neutral nature — a
        materially more realistic damage projection when the replay itself
        never reveals the real spread, still clearly an assumption (see
        explanation_system.txt's guidance on DamageResult.description).

        ``boosts``, when given, is a CONFIRMED (never Chaos-guessed) stat
        stage snapshot — e.g. {"atk": -1} from an observed Intimidate — for
        the exact moment this particular calc represents. Unlike
        ability/item/nature/EVs, this is never back-filled from Chaos (a
        stage is a point-in-time battle fact, not a "typical set" property);
        the caller (TurnReplaySimulator) is the one walking the timeline in
        order and knows the real value, so this method only ever applies
        exactly what it's given."""
        summary = meta.pokemon_stats.get(mon.species)
        data = mon.model_dump()
        if summary is not None:
            if not data.get("ability") and summary.top_abilities:
                data["ability"] = next(iter(summary.top_abilities))
            if not data.get("item") and summary.top_items:
                data["item"] = next(iter(summary.top_items))
            if not data.get("nature") and summary.top_spread_nature:
                data["nature"] = summary.top_spread_nature
            if data.get("evs") is None and summary.top_spread_evs is not None:
                data["evs"] = summary.top_spread_evs.model_dump()
        if statuses and not data.get("status") and mon.species in statuses:
            data["status"] = statuses[mon.species]
        if boosts:
            data["boosts"] = dict(boosts)
        return PokemonSet.model_validate(data)

    def _candidate_moves(self, mon: PokemonSet) -> list[str]:
        """Moves this Pokemon was actually observed using this game — never a guess."""
        return list(mon.moves)

    def _resolves(self, species: str) -> bool:
        """Cached check: does the installed calc engine have real stat data
        for this exact forme string? (one tiny IPC round-trip per distinct
        forme per evaluator lifetime, not per turn/verdict)."""
        key = (self._gen, species)
        cached = self._forme_resolve_cache.get(key)
        if cached is None:
            try:
                cached = self._calc.forme_resolves(self._gen, species)
            except CalcEngineError:
                cached = False
            self._forme_resolve_cache[key] = cached
        return cached

    def forme_caveat(self, attacker: PokemonSet, defender: PokemonSet | None = None) -> str:
        """Explain when a calc fell back to a Pokemon's BASE stats despite it
        being observed in a different in-battle forme (e.g. Mega Evolution)
        this game — this only happens when the installed calc engine's dex
        genuinely has no data for that exact forme (checked live, not
        assumed); when it does, the calc already used the real forme's
        stats and no caveat is needed.
        """
        notes: list[str] = []
        pairs = ((attacker, "attacker"),) if defender is None else (
            (attacker, "attacker"), (defender, "defender")
        )
        for mon, role in pairs:
            if mon.battle_formes and not self._resolves(mon.battle_formes[-1]):
                notes.append(
                    f"{mon.species} (the {role}) was also seen in-battle as "
                    f"{', '.join(mon.battle_formes)}; this calc uses {mon.species}'s "
                    "base stats because the calc engine has no data for that form, "
                    "so the real number may differ."
                )
        return " ".join(notes)

    def _best_move_verdict(
        self, attacker: PokemonSet, defender: PokemonSet, field: dict[str, Any]
    ) -> tuple[str, DamageResult] | None:
        best: tuple[str, DamageResult] | None = None
        for move in self._candidate_moves(attacker):
            try:
                result = self._calc.calculate(
                    CalcRequest(
                        gen=self._gen, attacker=attacker, defender=defender,
                        move=move, field=field,
                    )
                )
            except CalcEngineError:
                continue
            if best is None or result.max_percent > best[1].max_percent:
                best = (move, result)
        return best

    def _safe_speed(
        self, attacker: PokemonSet, defender: PokemonSet, field: dict[str, Any]
    ) -> SpeedComparison | None:
        try:
            return self._calc.compare_speed(
                CalcRequest(
                    gen=self._gen, attacker=attacker, defender=defender,
                    move=_DEFAULT_MOVE, field=field,
                )
            )
        except CalcEngineError:
            return None

    @staticmethod
    def _field_for(
        game_state: GameState, attacker_player: str | None, defender_player: str | None
    ) -> dict[str, Any]:
        """Build the @smogon/calc field spec for a matchup from the log ledger.

        Tailwind is applied to whichever side actually had it up during the game
        (post-game speed-tier reasoning); Trick Room and weather likewise.
        """
        field = game_state.field
        if field is None:
            return {}
        spec: dict[str, Any] = {}
        if attacker_player and field.had_tailwind(attacker_player):
            spec["attackerTailwind"] = True
        if defender_player and field.had_tailwind(defender_player):
            spec["defenderTailwind"] = True
        if field.had_trick_room():
            spec["trickRoom"] = True
        if field.weather:
            spec["weather"] = field.weather
        return spec

    def evaluate(
        self, game_state: GameState, selection: SelectionPlan, meta: MetaContext
    ) -> list[MatchupVerdict]:
        """Return one deterministic verdict per selected matchup (field-aware)."""
        sets = self.index_sets(game_state)
        side_of = game_state.side_of()
        statuses = game_state.field.statuses if game_state.field else {}
        verdicts: list[MatchupVerdict] = []
        for attacker_name, defender_name in selection.matchups:
            attacker = self.enrich_set(
                sets.get(attacker_name) or PokemonSet(species=attacker_name), meta, statuses
            )
            defender = self.enrich_set(
                sets.get(defender_name) or PokemonSet(species=defender_name), meta, statuses
            )
            field = self._field_for(
                game_state, side_of.get(attacker_name), side_of.get(defender_name)
            )
            best = self._best_move_verdict(attacker, defender, field)
            if best is None:
                continue
            best_move, best_damage = best
            verdicts.append(
                MatchupVerdict(
                    attacker=attacker_name,
                    defender=defender_name,
                    best_move=best_move,
                    best_damage=best_damage,
                    speed=self._safe_speed(attacker, defender, field),
                    stat_caveat=self.forme_caveat(attacker, defender),
                )
            )
        return verdicts


def collect_strategies(
    provider: StrategyKnowledgeProvider,
    species: Sequence[str],
    metagame: str | None = None,
    question: str | None = None,
) -> list[SmogonStrategy]:
    """Gather Smogon strategy knowledge, skipping unavailable species.

    `question` (the user's actual question, when known) is passed through so
    a semantic-retrieval-capable provider can pick the most relevant Smogon
    analysis passages for it — see `StrategyKnowledgeProvider.get_strategy`.
    """
    strategies: list[SmogonStrategy] = []
    for name in species:
        try:
            strategies.append(
                provider.get_strategy(name, metagame=metagame, question=question)
            )
        except StrategyKnowledgeError:
            continue
    return strategies
