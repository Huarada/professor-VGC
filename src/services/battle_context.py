"""Helpers that turn a GameState into prompt-ready battle context.

Shared by both orchestration backends so the selection stage sees the same
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
    """Every species to describe in the metagame context.

    Covers ALL Pokemon that were in play (brought by both sides), not just the
    leads or the selection focus, so the AI sees Chaos/Smogon data for the whole
    game. Any extra focus species are appended for completeness.
    """
    seen: dict[str, None] = {}
    for name in candidate_species(game_state):
        seen.setdefault(name, None)
    for name in focus or []:
        seen.setdefault(name, None)
    return list(seen.keys())


def outcome_summary(game_state: GameState) -> str:
    """Human-readable, strictly-ordered ground-truth result for prompts.

    Renders the exact action timeline so the AI never has to guess turn order
    or causality. A Pokemon with no "used <move>" line before it faints simply
    did not get to act.
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

    # Explicit roster per side, stated ONCE, up front — the single
    # authoritative anchor for "who is on which side". Reported: without
    # this, the explanation model had to infer side membership purely by
    # tracking each timeline event's own inline p1/p2 prefix across a long
    # game, and got it wrong in synthesis (crediting a losing side's own
    # Pokemon to the winning side). Every event line below still carries
    # its own prefix too (defense in depth), but this line means getting
    # it right no longer depends on the model tracking it consistently
    # across the whole timeline on its own.
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
    if field.weather:
        labels.append(f"weather {field.weather}")
    return labels
