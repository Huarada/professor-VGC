"""Helpers that turn a GameState into prompt-ready battle context.

Shared by every orchestration backend so the selection stage sees the same
rosters and the explanation stage sees the same ground-truth ordered timeline.
"""

from __future__ import annotations

from src.domain.models import GameState


def rosters(game_state: GameState) -> dict[str, list[str]]:
    """Map player id -> species brought (fallback to the full team roster)."""
    result: dict[str, list[str]] = {}
    for side in game_state.sides:
        brought = side.brought()
        if not brought:
            brought = [mon.species for mon in side.team]
        result[side.player] = brought
    return result


def candidate_species(game_state: GameState) -> list[str]:
    """Flat, de-duplicated list of the species worth analyzing."""
    seen: dict[str, None] = {}
    for species in rosters(game_state).values():
        for name in species:
            seen.setdefault(name, None)
    return list(seen.keys()) or game_state.involved_species()


def context_species(game_state: GameState, focus: list[str] | None = None) -> list[str]:
    """Every species in play (both sides), plus any extra focus species."""
    seen: dict[str, None] = {}
    for name in candidate_species(game_state):
        seen.setdefault(name, None)
    for name in focus or []:
        seen.setdefault(name, None)
    return list(seen.keys())


def outcome_summary(game_state: GameState) -> str:
    """The strictly ordered ground-truth timeline for prompts; a Pokemon with no
    "used <move>" line before fainting did not act.
    """
    outcome = game_state.outcome
    if outcome is None:
        return ""
    names = {side.player: (side.player_name or side.player) for side in game_state.sides}

    losses: dict[str, int] = {}
    for ko in outcome.kos:
        losses[ko.player] = losses.get(ko.player, 0) + 1
    score = "; ".join(
        f"{names.get(p, p)} lost {c}" for p, c in sorted(losses.items())
    )

    lines: list[str] = []
    if outcome.forfeited_name:
        lines.append(
            f"IMPORTANT — {outcome.forfeited_name} ({outcome.forfeited_player or '?'}) "
            "FORFEITED this game. It did NOT end by their team being defeated in play: "
            "do not narrate the win as 'earned' through strategy beyond what the actual "
            "timeline below shows, do not invent a final score from a full team wipeout, "
            "and do not imply the forfeiting side's un-brought/still-alive Pokemon lost a "
            "fight they never had."
        )
    if outcome.winner_name:
        lines.append(f"Winner: {outcome.winner_name} ({outcome.winner_player or '?'}).")
    if score:
        lines.append("Score — " + score + ".")
    lines.append(f"Total turns: {outcome.turns}.")

    # Rosters stated once up front, so the model never infers sides from the
    # per-event prefixes (it once credited a losing side's Pokemon to the winner).
    side_rosters = rosters(game_state)
    for side in game_state.sides:
        roster = side_rosters.get(side.player, [])
        if roster:
            lines.append(
                f"{side.player} roster ({names.get(side.player, side.player)}): "
                + ", ".join(roster) + "."
            )

    if outcome.events:
        lines.append("")
        lines.append(
            "Ordered timeline (exact order of actions; a Pokemon with no 'used' "
            "line before it faints did NOT act):"
        )
        current_turn = None
        for event in outcome.events:
            if event.turn != current_turn:
                current_turn = event.turn
                label = "Leads" if current_turn == 0 else f"Turn {current_turn}"
                conds = _field_labels(game_state, current_turn)
                suffix = f" [{'; '.join(conds)}]" if conds else ""
                lines.append(f"{label}{suffix}:")
            lines.append(f"  - {event.text}")
    return "\n".join(lines)


def _field_labels(game_state: GameState, turn: int) -> list[str]:
    """Field conditions active on a given turn, for timeline annotations."""
    field = game_state.field
    if field is None:
        return []
    labels: list[str] = []
    for side in game_state.sides:
        if field.tailwind_active(side.player, turn):
            labels.append(f"Tailwind {side.player}")
    if field.trick_room_active(turn):
        labels.append("Trick Room")
    labels.extend(f"weather {name}" for name in field.weather_on(turn))
    labels.extend(f"terrain {name}" for name in field.terrain_on(turn))
    for side in game_state.sides:
        labels.extend(f"{name} {side.player}" for name in field.screens_on(side.player, turn))
    return labels
