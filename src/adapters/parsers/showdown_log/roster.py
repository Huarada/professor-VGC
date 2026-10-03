"""Who is who: players, their rosters and the stable identity of each Pokemon.

Showdown references a Pokemon by ``<player><slot>: <nick>``; the roster turns
that into a stable ``(player, key)`` identity that survives a Mega Evolution
or another in-battle forme change, so a Pokemon's move history never
fragments across appearances.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.adapters.parsers.showdown_log.protocol import split_ref
from src.domain.models import PokemonSet, SideState

Key = tuple[str, str]  # (player, roster identity)


@dataclass
class MonDraft:
    """Mutable builder for one roster entry; frozen into a PokemonSet at the end."""

    species: str
    level: int = 50
    ability: str | None = None
    item: str | None = None  # ORIGINAL held item, once revealed
    moves: list[str] = field(default_factory=list)
    battle_formes: list[str] = field(default_factory=list)

    def build(self) -> PokemonSet:
        return PokemonSet(
            species=self.species, level=self.level, ability=self.ability,
            item=self.item, moves=list(self.moves), battle_formes=list(self.battle_formes),
        )


class Roster:
    """Players, their Pokemon and the slot -> identity mapping."""

    def __init__(self) -> None:
        self.players: dict[str, dict[str, MonDraft]] = {}
        self.player_names: dict[str, str] = {}
        self.avatars: dict[str, str] = {}
        self.preview: dict[str, list[str]] = {}  # team preview, in order
        self.brought: dict[str, list[str]] = {}
        self.slot_species: dict[str, str] = {}  # "p1a" -> roster key

    def register(self, player: str, key: str, level: int = 50) -> MonDraft:
        bucket = self.players.setdefault(player, {})
        return bucket.setdefault(key, MonDraft(species=key, level=level))

    def resolve(self, ref: str) -> Key | None:
        """Roster identity for a protocol reference (slot first, then nick)."""
        split = split_ref(ref)
        if split is None:
            return None
        player, slot, nick = split
        return player, self.slot_species.get(player + slot) or nick

    @staticmethod
    def side_of_ref(ref: str) -> str:
        """Player id of a side reference such as ``"p1: Ash"``."""
        split = split_ref(ref)
        return split[0] if split else (ref.split(":")[0].strip() or "p?")

    def record_forme(self, player: str, key: str, observed: str) -> None:
        """Note that ``key`` was seen in-battle as a DIFFERENT species string
        (Mega Evolution / other forme change) than its registered identity."""
        mon = self.players.get(player, {}).get(key)
        if mon is not None and observed != mon.species and observed not in mon.battle_formes:
            mon.battle_formes.append(observed)

    def mark_brought(self, player: str, key: str) -> None:
        brought = self.brought.setdefault(player, [])
        if key not in brought:
            brought.append(key)

    def player_named(self, name: str | None) -> str | None:
        if not name:
            return None
        return next((p for p, n in self.player_names.items() if n == name), None)

    def sides(self) -> list[SideState]:
        return [
            SideState(
                player=player,
                player_name=self.player_names.get(player, ""),
                team=[draft.build() for draft in mons.values()],
                active=self.brought.get(player, []),
            )
            for player, mons in self.players.items()
        ]
