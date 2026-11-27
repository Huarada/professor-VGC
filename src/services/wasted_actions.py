"""What each move achieved, read from the log: actions lost against the opponent, and partner synergies.

The best misplays of a game are often not damage rolls (Fake Out into a Ghost, Sucker Punch against a Pokemon that
did not attack); the log marks them with `-immune`, `-fail`, `-miss` and a blocked hit (ADR-082). A spread move also
hits the user's own partner, usually on purpose (the partner is immune, protects, or has an ability that wants the
hit), so what happens to a partner is never a lost action; it is listed as a synergy (ADR-083).
"""

from __future__ import annotations

from typing import Literal

from src.domain.models import AllySynergy, BattleEvent, GameState, WastedAction

_UNAFFECTED: dict[str, Literal["immune", "missed"]] = {"immune": "immune", "missed": "missed"}
_GAINS = {"ability", "boosted", "healed"}  # the move did something useful even if it did nothing to the opponent


def _moves(game_state: GameState) -> list[BattleEvent]:
    outcome = game_state.outcome
    return [event for event in outcome.events if event.kind == "move" and event.move] if outcome else []


def _damaged_an_opponent(event: BattleEvent) -> bool:
    return any(hit.player != event.actor_player and not hit.blocked_by for hit in event.hits)


def find_wasted_actions(game_state: GameState) -> list[WastedAction]:
    """Moves that achieved nothing against the opponent. A spread move that damaged any opponent is not one."""
    found: list[WastedAction] = []
    for event in _moves(game_state):
        if _damaged_an_opponent(event) or any(effect.effect in _GAINS for effect in event.ally_effects):
            continue

        def lost(outcome: Literal["immune", "failed", "missed", "blocked"], target: str = "", player: str = "",
                 detail: str = "", event: BattleEvent = event) -> None:
            found.append(WastedAction(turn=event.turn, actor=event.actor, actor_player=event.actor_player,
                                      move=event.move, outcome=outcome, target=target, target_player=player, detail=detail))

        unaffected = [entry.partition(":") for entry in event.unaffected_targets]
        for effect in event.effects:
            kind = _UNAFFECTED.get(effect)
            if kind:
                for player, _, species in unaffected:
                    if player != event.actor_player:
                        lost(kind, species, player)
                if not unaffected:
                    lost(kind)
            elif effect == "failed":
                lost("failed")
        for hit in event.hits:
            if hit.blocked_by and hit.player != event.actor_player:
                lost("blocked", hit.species, hit.player, f"blocked by {hit.blocked_by}")
    return found


def find_ally_synergies(game_state: GameState) -> list[AllySynergy]:
    """What each move did for, or safely past, the user's own partner (one entry per move and partner)."""
    found: list[AllySynergy] = []
    for event in _moves(game_state):
        results: dict[str, list[str]] = {}
        for effect in event.ally_effects:
            text = {
                "immune": f"took no damage, immune{f' by {effect.detail}' if effect.detail else ''}",
                "protected": f"avoided it with {effect.detail}",
                "ability": f"its ability {effect.detail} activated",
                "boosted": f"its {effect.detail}",
                "healed": f"was healed{f' by {effect.detail}' if effect.detail else ''}",
            }[effect.effect]
            results.setdefault(effect.species, []).append(text)
        found += [AllySynergy(turn=event.turn, actor=event.actor, actor_player=event.actor_player, move=event.move,
                              ally=ally, result="; ".join(texts)) for ally, texts in results.items()]
    return found
