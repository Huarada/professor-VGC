"""The battle as it stood at the instant one move was used.

Every per-move re-check (damage of the move used, better moves/targets,
incoming threats, Protect/switch/speed-control options) reads the battle
through one :class:`MoveMoment`, so they all agree on the same facts: the
weather, terrain, screens, Helping Hand, HP, status, held item and stat
stages AT THAT MOVE — taken from the parser's per-move
:class:`~src.domain.models.BattleSnapshot`, never from the end of the game.

Events built without a snapshot (structured JSON input, unit tests) fall back
to the turn-level :class:`~src.domain.models.FieldConditions` windows and to
``side_of()`` for target ownership, which is the best that input allows.
"""

from __future__ import annotations

from src.domain.models import (
    BattleEvent,
    BattleSnapshot,
    CalcField,
    CalcRequest,
    GameState,
    MetaContext,
    PokemonSet,
    SideField,
)
from src.services.matchup_evaluator import MatchupEvaluator, SetIndex

Combatant = tuple[str, str]  # (player, species)
BoostLedger = dict[Combatant, dict[str, int]]


class MoveMoment:
    """Read-only view of the battle at one move event."""

    def __init__(
        self,
        *,
        game_state: GameState,
        event: BattleEvent,
        meta: MetaContext,
        sets: SetIndex,
        evaluator: MatchupEvaluator,
        boosts: BoostLedger,
        gen: int,
    ) -> None:
        self.event = event
        self.turn = event.turn
        self.actor: Combatant = (event.actor_player, event.actor)
        self._game = game_state
        self._state: BattleSnapshot | None = event.state
        self._meta = meta
        self._sets = sets
        self._evaluator = evaluator
        self._boosts = boosts
        self._gen = gen
        self._side_of = game_state.side_of()
        self._enriched: dict[Combatant, PokemonSet] = {}

    # -- Pokemon ------------------------------------------------------------ #

    def combatant(self, who: Combatant) -> PokemonSet:
        """The enriched set for ``who`` with its status/item/boosts at this move."""
        cached = self._enriched.get(who)
        if cached is None:
            player, species = who
            base = self._sets.get(who) or PokemonSet(species=species)
            status: str | None = None
            item: str | None = None
            if self._state is not None:
                mon = self._state.mon(player, species)
                status, item = mon.status, mon.item
            cached = self._evaluator.enrich_set(
                base, self._meta, status=status, item=item, boosts=self._boosts.get(who, {}),
            )
            self._enriched[who] = cached
        return cached

    @property
    def attacker(self) -> PokemonSet:
        return self.combatant(self.actor)

    def hp(self, who: Combatant) -> float | None:
        """HP percent of ``who`` right before this move (None = unknown)."""
        if self._state is None:
            return None
        return self._state.mon(*who).hp_percent

    def owner(self, species: str, *, opposing_to: str = "") -> str:
        """Player owning ``species``, preferring the side opposite ``opposing_to``
        (a mirror-match species exists on both sides)."""
        for side in self._game.sides:
            names = side.brought() or [mon.species for mon in side.team]
            if side.player != opposing_to and species in names:
                return side.player
        return self._side_of.get(species, "")

    def targets(self) -> list[Combatant]:
        """Every Pokemon this move hit or was blocked by, side-qualified."""
        if self.event.hits:
            return [(hit.player, hit.species) for hit in self.event.hits]
        names = list(self.event.targets) + [
            b for b in self.event.blocked if b not in self.event.targets
        ]
        return [(self.owner(name, opposing_to=self.actor[0]), name) for name in names]

    def opponents(self) -> list[Combatant]:
        """Opposing Pokemon on the field at this move (falls back to the
        move's own first target when the field isn't known)."""
        if self._state is not None:
            found = [
                (player, species)
                for player, species_list in self._state.active.items()
                if player != self.actor[0]
                for species in species_list
            ]
            if found:
                return found
        targets = [t for t in self.targets() if t[0] != self.actor[0]]
        return targets[:1]

    def ally(self, who: Combatant) -> Combatant | None:
        """The other Pokemon on ``who``'s side of the field, if any."""
        if self._state is None:
            return None
        player, species = who
        others = [s for s in self._state.active.get(player, []) if s != species]
        return (player, others[0]) if others else None

    def bench(self, player: str) -> list[str]:
        """Brought Pokemon of ``player`` that could switch in at this move."""
        if self._state is None:
            return []
        on_field = set(self._state.active.get(player, []))
        fainted = set(self._state.fainted.get(player, []))
        side = next((s for s in self._game.sides if s.player == player), None)
        brought = side.brought() if side is not None else []
        return [name for name in brought if name not in on_field and name not in fainted]

    # -- field -------------------------------------------------------------- #

    def _weather(self) -> str:
        if self._state is not None:
            return self._state.weather
        field = self._game.field
        active = field.weather_on(self.turn) if field else []
        return active[-1] if active else ""

    def _terrain(self) -> str:
        if self._state is not None:
            return self._state.terrain
        field = self._game.field
        active = field.terrain_on(self.turn) if field else []
        return active[-1] if active else ""

    def _screens(self, player: str) -> list[str]:
        if self._state is not None:
            return self._state.screens.get(player, [])
        field = self._game.field
        return field.screens_on(player, self.turn) if field else []

    def _tailwind(self, player: str) -> bool:
        field = self._game.field
        return bool(player and field and field.tailwind_active(player, self.turn))

    def trick_room(self) -> bool:
        field = self._game.field
        return bool(field and field.trick_room_active(self.turn))

    def _friend_guard(self, defender: Combatant) -> bool:
        ally = self.ally(defender)
        return ally is not None and (self.combatant(ally).ability or "") == "Friend Guard"

    def field(self, attacker: Combatant, defender: Combatant) -> CalcField:
        """Calc field for ``attacker`` hitting ``defender`` at this move."""
        screens = set(self._screens(defender[0]))
        return CalcField(
            weather=self._weather(),
            terrain=self._terrain(),
            trick_room=self.trick_room(),
            attacker_side=SideField(
                tailwind=self._tailwind(attacker[0]),
                helping_hand=attacker == self.actor and "helping hand" in self.event.effects,
            ),
            defender_side=SideField(
                tailwind=self._tailwind(defender[0]),
                reflect="Reflect" in screens,
                light_screen="Light Screen" in screens,
                aurora_veil="Aurora Veil" in screens,
                friend_guard=self._friend_guard(defender),
            ),
        )

    def request(self, attacker: Combatant, defender: Combatant, move: str) -> CalcRequest:
        """Damage request for ``attacker``'s ``move`` into ``defender`` right now."""
        return CalcRequest(
            gen=self._gen,
            attacker=self.combatant(attacker),
            defender=self.combatant(defender),
            move=move,
            field=self.field(attacker, defender),
            defender_hp_percent=self.hp(defender),
        )

    def conditions(self) -> list[str]:
        """Human-readable field conditions active at this move."""
        labels: list[str] = []
        for side in self._game.sides:
            if self._tailwind(side.player):
                labels.append(f"Tailwind {side.player}")
        if self.trick_room():
            labels.append("Trick Room")
        if self._weather():
            labels.append(f"weather {self._weather()}")
        if self._terrain():
            labels.append(f"terrain {self._terrain()}")
        for side in self._game.sides:
            labels.extend(f"{screen} {side.player}" for screen in self._screens(side.player))
        if "helping hand" in self.event.effects:
            labels.append(f"Helping Hand ({self.event.actor})")
        return labels
