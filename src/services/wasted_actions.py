"""Moves that did nothing, read from the log: an immune target, a failed move, a miss, a Protect that absorbed it.

The best misplays of a game are often not damage rolls (Fake Out into a Ghost, Sucker Punch against a Pokemon that
did not attack). The log marks them with `-immune`, `-fail`, `-miss` and a blocked hit; this lists them, per turn, so
the explanation cites them instead of guessing (ADR-082).
"""

from __future__ import annotations

from typing import Literal

from src.domain.models import BattleEvent, GameState, WastedAction

_OUTCOMES: dict[str, Literal["immune", "failed", "missed"]] = {"immune": "immune", "failed": "failed", "missed": "missed"}


def _opponent_of(player: str, game_state: GameState) -> str:
    return next((side.player for side in game_state.sides if side.player != player), "")


def _target_of(event: BattleEvent, game_state: GameState) -> tuple[str, str]:
    """The (species, player) a single-target move was aimed at, empty when it had several or none."""
    if len(event.unaffected_targets) == 1:
        player, _, species = event.unaffected_targets[0].partition(":")
        return species, player
    targets = list(dict.fromkeys(event.targets))
    if len(targets) != 1:
        return "", ""
    owners = {side.player for side in game_state.sides if targets[0] in (side.brought() or [m.species for m in side.team])}
    opposing = _opponent_of(event.actor_player, game_state)
    return targets[0], (opposing if opposing in owners else next(iter(owners)) if len(owners) == 1 else "")


def find_wasted_actions(game_state: GameState) -> list[WastedAction]:
    outcome = game_state.outcome
    if outcome is None:
        return []
    found: list[WastedAction] = []
    for event in outcome.events:
        if event.kind != "move" or not event.move:
            continue
        target, target_player = _target_of(event, game_state)
        for effect in event.effects:
            kind = _OUTCOMES.get(effect)
            if kind:
                found.append(WastedAction(turn=event.turn, actor=event.actor, actor_player=event.actor_player,
                                          move=event.move, outcome=kind, target=target, target_player=target_player))
        for hit in event.hits:
            if hit.blocked_by:
                found.append(WastedAction(turn=event.turn, actor=event.actor, actor_player=event.actor_player,
                                          move=event.move, outcome="blocked", target=hit.species, target_player=hit.player,
                                          detail=f"blocked by {hit.blocked_by}"))
    return found
