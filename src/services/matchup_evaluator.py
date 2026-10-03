"""Deterministic damage + speed matchup evaluation, shared by every backend
through the evidence stage.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.exceptions import CalcEngineError, StrategyKnowledgeError
from src.domain.interfaces import CalcEngineAdapter, StrategyKnowledgeProvider
from src.domain.models import (
    CalcField,
    CalcRequest,
    DamageResult,
    GameState,
    MatchupVerdict,
    MetaContext,
    PokemonSet,
    SelectionPlan,
    SideField,
    SmogonStrategy,
    SpeedComparison,
)

SetIndex = dict[tuple[str, str], PokemonSet]
"""(player, species) -> the best-known set. Side-qualified so a mirror match
(the same species on both sides) keeps two distinct Pokemon."""

_DEFAULT_MOVE = "Tackle"


class MatchupEvaluator:
    """Runs deterministic calcs for a selection plan against a game state."""

    def __init__(self, calc_engine: CalcEngineAdapter, default_gen: int = 9) -> None:
        self._calc = calc_engine
        self._gen = default_gen
        self._forme_resolve_cache: dict[tuple[int, str], bool] = {}
        self._move_damaging_cache: dict[tuple[int, str], bool] = {}

    def index_sets(self, game_state: GameState) -> SetIndex:
        """Map (player, species) -> best-known set from the battle state."""
        index: SetIndex = {}
        for side in game_state.sides:
            for mon in side.team:
                index.setdefault((side.player, mon.species), mon)
        return index

    def enrich_set(
        self,
        mon: PokemonSet,
        meta: MetaContext,
        *,
        status: str | None = None,
        boosts: dict[str, int] | None = None,
        item: str | None = None,
    ) -> PokemonSet:
        """Back-fill hidden ability/item/nature/EVs from Chaos, then apply the
        confirmed battle facts at this calc's moment.

        Unrevealed nature/EVs use the tier's most-used spread (an assumption the
        description states), not 0 EVs.

        Args:
            mon: The best-known set (revealed ability/item/moves from the log).
            meta: Chaos context for the back-fill.
            status: Non-volatile status at this moment ("" = healthy).
            boosts: Stat stages at this moment (never back-filled).
            item: Item at this moment: a name, "" for confirmed none (also stops
                the Chaos guess), or None to keep the set's item / Chaos guess.
        """
        summary = meta.pokemon_stats.get(mon.species)
        data = mon.model_dump()
        if item is not None:
            data["item"] = item or None
        if summary is not None:
            if not data.get("ability") and summary.top_abilities:
                data["ability"] = next(iter(summary.top_abilities))
            if item is None and not data.get("item") and summary.top_items:
                data["item"] = next(iter(summary.top_items))
            if not data.get("nature") and summary.top_spread_nature:
                data["nature"] = summary.top_spread_nature
            if data.get("evs") is None and summary.top_spread_evs is not None:
                data["evs"] = summary.top_spread_evs.model_dump()
        if status is not None:
            data["status"] = status or None
        if boosts:
            data["boosts"] = dict(boosts)
        return PokemonSet.model_validate(data)

    def _candidate_moves(self, mon: PokemonSet) -> list[str]:
        """Moves this Pokemon was actually observed using this game — never a guess."""
        return list(mon.moves)

    def _resolves(self, species: str) -> bool:
        """Cached: does the engine have stats for this exact forme?"""
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
        """A note when a forme seen this game (e.g. a Mega) had no engine stats, so
        the calc used base stats; empty otherwise.
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

    def is_damaging(self, move: str) -> bool:
        """Whether the engine classifies ``move`` as an attack (cached; unreachable
        engine = damaging, so the calc decides).
        """
        key = (self._gen, move)
        cached = self._move_damaging_cache.get(key)
        if cached is None:
            try:
                cached = self._calc.move_info(self._gen, move).is_damaging
            except CalcEngineError:
                cached = True
            self._move_damaging_cache[key] = cached
        return cached

    def _best_move_verdict(
        self, attacker: PokemonSet, defender: PokemonSet, field: CalcField
    ) -> tuple[str, DamageResult] | None:
        best: tuple[str, DamageResult] | None = None
        for move in self._candidate_moves(attacker):
            if not self.is_damaging(move):
                continue
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
        self, attacker: PokemonSet, defender: PokemonSet, field: CalcField
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
    ) -> CalcField:
        """Whole-game field for a post-game verdict: Tailwind/Trick Room where they
        were used, plus the dominant weather/terrain. Per-move conditions belong
        to the per-turn re-checks.
        """
        field = game_state.field
        if field is None:
            return CalcField()
        return CalcField(
            weather=field.dominant_weather(),
            terrain=field.dominant_terrain(),
            trick_room=field.had_trick_room(),
            attacker_side=SideField(
                tailwind=bool(attacker_player and field.had_tailwind(attacker_player))
            ),
            defender_side=SideField(
                tailwind=bool(defender_player and field.had_tailwind(defender_player))
            ),
        )

    @staticmethod
    def resolve_players(
        game_state: GameState, attacker: str, defender: str
    ) -> tuple[str, str]:
        """Owning players of a cross-side matchup, mirror-safe: the defender
        is looked up on the side OPPOSITE the attacker first."""
        brought = {
            side.player: set(side.brought() or [mon.species for mon in side.team])
            for side in game_state.sides
        }
        side_of = game_state.side_of()
        attacker_player = side_of.get(attacker, "")
        defender_player = next(
            (p for p, names in brought.items() if p != attacker_player and defender in names),
            side_of.get(defender, ""),
        )
        return attacker_player, defender_player

    def evaluate(
        self, game_state: GameState, selection: SelectionPlan, meta: MetaContext
    ) -> list[MatchupVerdict]:
        """Return one deterministic verdict per selected matchup (field-aware)."""
        sets = self.index_sets(game_state)
        statuses = game_state.field.final_statuses if game_state.field else {}
        verdicts: list[MatchupVerdict] = []
        for attacker_name, defender_name in selection.matchups:
            attacker_player, defender_player = self.resolve_players(
                game_state, attacker_name, defender_name
            )
            attacker = self.enrich_set(
                sets.get((attacker_player, attacker_name)) or PokemonSet(species=attacker_name),
                meta, status=statuses.get(attacker_player, {}).get(attacker_name),
            )
            defender = self.enrich_set(
                sets.get((defender_player, defender_name)) or PokemonSet(species=defender_name),
                meta, status=statuses.get(defender_player, {}).get(defender_name),
            )
            field = self._field_for(game_state, attacker_player, defender_player)
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
    """Smogon strategy for each species (unavailable ones skipped); ``question``
    lets a semantic provider pick the most relevant passages.
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
