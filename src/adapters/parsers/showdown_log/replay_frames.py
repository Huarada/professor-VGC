"""The battle panel's turn-by-turn view, built from the same parse as the
analysis (ADR-037): a frame of every Pokemon's state at each turn boundary,
plus the field windows the parser already tracks."""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.parsers.showdown_log.combatants import CombatantLedger
from src.adapters.parsers.showdown_log.roster import Key, Roster
from src.domain.models import FieldConditions, FieldWindow, GameState
from src.domain.replay_view_models import BattleReplay, ReplayPokemonState, ReplayTurnSnapshot

_LOG_KINDS = ("switch", "move", "faint")


@dataclass(frozen=True)
class TurnFrame:
    """Every Pokemon's state at the end of one turn (``turn=0`` = leads)."""

    turn: int
    slots: dict[str, str]  # "p1a" -> roster key (a fainted Pokemon keeps its slot)
    seen: dict[str, list[str]]  # player -> Pokemon brought so far
    hp: dict[Key, float]
    status: dict[Key, str]
    forme: dict[Key, str]
    boosts: dict[Key, dict[str, int]]

    @classmethod
    def capture(cls, turn: int, roster: Roster, combatants: CombatantLedger) -> TurnFrame:
        return cls(
            turn=turn,
            slots=dict(roster.slot_species),
            seen={player: list(keys) for player, keys in roster.brought.items()},
            hp=dict(combatants.hp),
            status=dict(combatants.status),
            forme=dict(combatants.forme),
            boosts={key: dict(stages) for key, stages in combatants.boosts.items()},
        )


def build_battle_replay(
    frames: list[TurnFrame], roster: Roster, rooms: list[FieldWindow], game: GameState
) -> BattleReplay:
    """The panel's ``BattleReplay`` from the frames and the closed parse."""
    field = game.field or FieldConditions()
    outcome = game.outcome
    events = outcome.events if outcome else []
    players = list(roster.player_names) or list(roster.players)
    snapshots = [
        ReplayTurnSnapshot(
            turn=frame.turn,
            active=_active(frame),
            pokemon=_pokemon(frame),
            log=[e.text for e in events if e.turn == frame.turn and e.kind in _LOG_KINDS],
            conditions=_conditions(frame.turn, players, field, rooms),
        )
        for frame in frames
    ]
    return BattleReplay(
        player_names=dict(roster.player_names),
        avatars=dict(roster.avatars),
        team={player: list(species) for player, species in roster.preview.items()},
        winner_player=outcome.winner_player if outcome else None,
        forfeited_player=outcome.forfeited_player if outcome else None,
        snapshots=snapshots,
    )


def _active(frame: TurnFrame) -> dict[str, list[str]]:
    active: dict[str, list[str]] = {}
    for slot_ref in sorted(frame.slots):
        active.setdefault(slot_ref[:2], []).append(frame.slots[slot_ref])
    return active


def _pokemon(frame: TurnFrame) -> dict[str, dict[str, ReplayPokemonState]]:
    pokemon: dict[str, dict[str, ReplayPokemonState]] = {}
    for player, keys in frame.seen.items():
        for name in keys:
            key = (player, name)
            hp = frame.hp.get(key, 100.0)
            pokemon.setdefault(player, {})[name] = ReplayPokemonState(
                species=name,
                forme=frame.forme.get(key, ""),
                hp_percent=hp,
                fainted=hp <= 0.0,
                status=frame.status.get(key, ""),
                boosts=dict(frame.boosts.get(key, {})),
            )
    return pokemon


def _last_on(windows: list[FieldWindow], turn: int) -> str:
    """The condition in effect by the end of ``turn`` (or the last one that
    expired during it): a replaced weather or terrain is not shown."""
    covering = [w for w in windows if w.covers(turn)]
    return covering[-1].name if covering else ""


def _conditions(
    turn: int, players: list[str], field: FieldConditions, rooms: list[FieldWindow]
) -> list[str]:
    labels = [f"Tailwind {p}" for p in players if field.tailwind_active(p, turn)]
    if field.trick_room_active(turn):
        labels.append("Trick Room")
    weather = _last_on(field.weather, turn)
    if weather:
        labels.append(f"weather {weather}")
    terrain = _last_on(field.terrain, turn)
    if terrain:
        labels.append(f"terrain {terrain}")
    labels += sorted({w.name for w in rooms if w.covers(turn)})
    labels += sorted(f"{name} {p}" for p in field.screens for name in field.screens_on(p, turn))
    return labels
