"""Which turn decided the game: a deterministic reading of the logged HP and faints.

The score is material, not narrative: for each side the sum of the HP percent of every Pokemon it brought
(a fainted one counts 0), sampled at the end of each turn. The explanation narrates the result and never
chooses the turn itself (it once picked a KO on a 2%-HP Pokemon).
"""

from __future__ import annotations

import re
from typing import Literal

from src.domain.models import BattleEvent, GameState, TurningPoint, TurnMaterial

_MARGIN = 0.5  # half a Pokemon's worth of HP: smaller leads flip with one roll or a chip tick
_ASKS_DECIDER = re.compile(
    r"turning|decided|decisive|deciding|swung|swing|won or lost|lost or won|\bwhy\b.*\b(?:won|lost|win|lose)\b"
    r"|general|overall|full review|whole game|virada|virou|decidiu|decisiv|por\s*que\b.*\b(?:perd|ganh|venc)"
    r"|an[aá]lise geral", re.I)


def asks_turning_point(question: str) -> bool:
    """True when the question asks what decided the game, why it was won or lost, or for a general review (ADR-087)."""
    return not question.strip() or bool(_ASKS_DECIDER.search(question))


def _bases(game_state: GameState) -> dict[tuple[str, str], str]:
    """(player, forme or species) -> the species the side brought, so a Mega shares its base HP."""
    mapping: dict[tuple[str, str], str] = {}
    for side in game_state.sides:
        for mon in side.team:
            mapping[(side.player, mon.species)] = mon.species
            for forme in mon.battle_formes:
                mapping[(side.player, forme)] = mon.species
    return mapping


def _material_by_turn(game_state: GameState) -> list[tuple[int, dict[tuple[str, str], float], list[str]]]:
    outcome = game_state.outcome
    assert outcome is not None
    base = _bases(game_state)
    hp: dict[tuple[str, str], float] = {
        (side.player, name): 100.0 for side in game_state.sides for name in side.brought()
    }

    def key(player: str, species: str) -> tuple[str, str] | None:
        found = (player, base.get((player, species), species))
        return found if found in hp else None

    def set_hp(player: str, species: str, value: float) -> None:
        if (slot := key(player, species)) is not None:
            hp[slot] = max(0.0, min(100.0, value))

    by_turn: dict[int, list[BattleEvent]] = {}
    for event in outcome.events:
        by_turn.setdefault(event.turn, []).append(event)
    kos_by_turn: dict[int, list[tuple[str, str]]] = {}
    for ko in outcome.kos:
        kos_by_turn.setdefault(ko.turn, []).append((ko.player, ko.fainted))

    series = []
    for turn in sorted(t for t in set(by_turn) | set(kos_by_turn) if t > 0):
        for event in by_turn.get(turn, []):
            if event.state is not None:
                for ref, mon in event.state.mons.items():
                    player, _, species = ref.partition(":")
                    if mon.hp_percent is not None:
                        set_hp(player, species, mon.hp_percent)
            for hit in event.hits:
                if hit.hp_after is not None:
                    set_hp(hit.player, hit.species, hit.hp_after)
        faints = []
        for player, species in kos_by_turn.get(turn, []):
            set_hp(player, species, 0.0)
            faints.append(f"{player} {species} fainted")
        series.append((turn, dict(hp), faints))
    return series


def find_turning_point(game_state: GameState) -> TurningPoint | None:
    """None without a winner (forfeits and unfinished logs have no meaningful turning point)."""
    outcome = game_state.outcome
    if outcome is None or not outcome.winner_player or outcome.forfeited_player or not outcome.events:
        return None
    winner = outcome.winner_player
    if len(game_state.sides) != 2 or winner not in {side.player for side in game_state.sides}:
        return None
    names = {side.player: side.player_name or side.player for side in game_state.sides}
    rows: list[TurnMaterial] = []
    previous_lead = 0.0
    for turn, hp, faints in _material_by_turn(game_state):
        mine = [v for (player, _), v in hp.items() if player == winner]
        theirs = [v for (player, _), v in hp.items() if player != winner]
        lead = (sum(mine) - sum(theirs)) / 100
        rows.append(TurnMaterial(
            turn=turn, winner_alive=sum(v > 0 for v in mine), loser_alive=sum(v > 0 for v in theirs),
            winner_hp=round(sum(mine), 1), loser_hp=round(sum(theirs), 1), lead=round(lead, 2),
            swing=round(lead - previous_lead, 2), faints=faints))
        previous_lead = lead
    if not rows:
        return None
    rule: Literal["lead_held_to_the_end", "largest_swing"]
    held = len(rows)
    while held > 0 and rows[held - 1].lead >= _MARGIN:
        held -= 1
    if held < len(rows):
        chosen, rule = rows[held], "lead_held_to_the_end"
    else:
        chosen, rule = max(rows, key=lambda row: (row.swing, -row.turn)), "largest_swing"
    others = sorted((row for row in rows if row.turn != chosen.turn), key=lambda row: -abs(row.swing))[:2]
    kept = ("and kept at least half a Pokemon's worth of HP ahead until the end of the game." if
            rule == "lead_held_to_the_end" else "; no earlier turn held a clear lead, so this moved it the most.")
    summary = (f"After turn {chosen.turn} {names[winner]} led {chosen.winner_alive} Pokemon to {chosen.loser_alive} "
               f"({chosen.winner_hp:g}% against {chosen.loser_hp:g}% total HP) {kept}" if rule == "lead_held_to_the_end"
               else f"After turn {chosen.turn} {names[winner]} led {chosen.winner_alive} Pokemon to {chosen.loser_alive} "
                    f"({chosen.winner_hp:g}% against {chosen.loser_hp:g}% total HP){kept}")
    return TurningPoint(rule=rule, turn=chosen.turn, winner_player=winner, winner_name=names[winner],
                        summary=summary, candidates=[chosen, *others])
