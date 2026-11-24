"""Deterministic check that an answer does not credit a Pokemon to the wrong side.

The model once advised "switching Basculegion to Kingambit" when Kingambit was on the opposing team. A
prompt rule did not stop it, so the answer is checked against the roster lines: only claims that are
unambiguous from the text are flagged (a possessive, a p1/p2 prefix, a switch between two sides).
"""

from __future__ import annotations

import re

from src.domain.models import GameState
from src.services.battle_context import rosters

MARK = r"[*_`]*"


def owners_by_species(game_state: GameState) -> dict[str, frozenset[str]]:
    """Species or forme -> the players that brought it (a mirror species has two owners and is never flagged)."""
    owners: dict[str, set[str]] = {}
    brought = rosters(game_state)
    for side in game_state.sides:
        for name in brought.get(side.player, []):
            owners.setdefault(name, set()).add(side.player)
        for mon in side.team:
            if mon.species in brought.get(side.player, []):
                for forme in mon.battle_formes:
                    owners.setdefault(forme, set()).add(side.player)
    return {name: frozenset(players) for name, players in owners.items()}


def alternation(words: list[str]) -> str:
    return "|".join(re.escape(word) for word in sorted(set(words), key=len, reverse=True))


def side_attribution_issues(answer: str, game_state: GameState) -> list[str]:
    owners = owners_by_species(game_state)
    if len(game_state.sides) != 2 or not owners:
        return []
    labels = {side.player: side.player_name or side.player for side in game_state.sides}
    who: dict[str, str] = {}
    for player, name in labels.items():
        who[name.casefold()] = player
        who[player.casefold()] = player
    species = alternation(list(owners))
    holder = alternation(list(who))
    issues: list[str] = []

    def sole_owner(name: str) -> str | None:
        players = owners.get(name, frozenset())
        return next(iter(players)) if len(players) == 1 else None

    def check(claimed_player: str, mon: str, quote: str) -> None:
        owner = sole_owner(mon)
        if owner is not None and owner != claimed_player:
            issues.append(f'"{quote}": {mon} belongs to {labels[owner]} ({owner}), not {labels[claimed_player]}.')

    possessive = re.compile(rf"(?<![\w-])({holder}){MARK}(?:'s|’s){MARK}\s+{MARK}({species})(?![\w-])", re.I)
    for match in possessive.finditer(answer):
        check(who[match.group(1).casefold()], canonical(match.group(2), owners), match.group(0))
    prefixed = re.compile(rf"(?<![\w-])(p[12])\s+{MARK}({species})(?![\w-])", re.I)
    for match in prefixed.finditer(answer):
        if match.group(1).casefold() in who:
            check(who[match.group(1).casefold()], canonical(match.group(2), owners), match.group(0))
    switch = re.compile(
        rf"(?<![\w-])(?:switch|swap)\w*\s+{MARK}({species}){MARK}\s+(?:out\s+)?(?:to|into|for|with)\s+{MARK}({species})(?![\w-])",
        re.I)
    for match in switch.finditer(answer):
        first, second = (canonical(match.group(i), owners) for i in (1, 2))
        left, right = sole_owner(first), sole_owner(second)
        if left is not None and right is not None and left != right:
            issues.append(f'"{match.group(0)}": {first} and {second} are on opposing teams, so one cannot replace the other.')
    return list(dict.fromkeys(issues))


def canonical(text: str, owners: dict[str, frozenset[str]]) -> str:
    folded = text.casefold()
    return next((name for name in owners if name.casefold() == folded), text)


def narration_issues(answer: str, game_state: GameState) -> list[str]:
    """Everything in a BO1 answer that contradicts the log: wrong side, or a faint on the wrong turn."""
    from src.services.faint_timing import faint_timing_issues  # noqa: PLC0415 - faint_timing imports this module

    return side_attribution_issues(answer, game_state) + faint_timing_issues(answer, game_state)


def correction_note(issues: list[str]) -> str:
    lines = "\n".join(f"- {issue}" for issue in issues)
    return ("CORRECTION: your previous answer contradicts the battle log:\n" + lines +
            "\nThe '<p1/p2> roster' lines, the ordered timeline and turning_point.candidates[].faints are the only "
            "authority on sides and on which turn each Pokemon fainted. Rewrite the full answer so every Pokemon is "
            "attributed to its own side, advice names only Pokemon from the advised player's roster, and every faint "
            "is placed on the turn the log shows.")
