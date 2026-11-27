"""Deterministic check of "Player A's X used M on turn N" claims against the log's own move list (ADR-082).

gpt-4o-mini credited a failed Sucker Punch from one side's Kingambit to the other side's, and put Protect on a turn the
Pokemon did not use it. A sentence that names exactly one turn, a Pokemon (optionally with its owner) and "used <move>"
is read: the log must show that Pokemon using that move on that turn, and, when the sentence names the target right
after the move, that target. Counterfactual sentences ("should have used", "would") are not checked.
"""

from __future__ import annotations

import re

from src.domain.models import BattleEvent, GameState
from src.services.faint_timing import _forme_bases, clause_around, sentences, turn_groups
from src.services.side_attribution import MARK, alternation, canonical, owners_by_species

_HYPOTHETICAL = re.compile(r"\b(?:would|could|might|may|should|can|if|chance|potential|possible|likely|rather)\b", re.I)
_INSTEAD = re.compile(r"\b(?:instead\s+of|rather\s+than)\b.*$", re.I)
_THIS_TURN = re.compile(r"\b(?:this|that|the same)\s+(?:\w+\s+)?turn\b", re.I)
_USED = r"(?:used|uses|using|chose|launched|selected|clicked)"


def _moves_used(game_state: GameState) -> list[BattleEvent]:
    outcome = game_state.outcome
    return [event for event in outcome.events if event.kind == "move" and event.move] if outcome else []


def _hit_species(event: BattleEvent) -> set[tuple[str, str]]:
    """(player, species) of every target the log ties to this move: damaged, blocked, immune or missed."""
    found = {(hit.player, hit.species) for hit in event.hits}
    for entry in event.unaffected_targets:
        player, _, species = entry.partition(":")
        found.add((player, species))
    return found


def action_claim_issues(answer: str, game_state: GameState) -> list[str]:
    events = _moves_used(game_state)
    owners = owners_by_species(game_state)
    if not events or not owners or len(game_state.sides) != 2:
        return []
    labels = {side.player: side.player_name or side.player for side in game_state.sides}
    who = {name.casefold(): player for player, name in labels.items()} | {player: player for player in labels}
    base = _forme_bases(game_state)
    moves = alternation(sorted({event.move for event in events}))
    holder, species = alternation(list(who)), alternation(list(owners))
    possessive = rf"(?:{MARK}(?P<who>{holder}){MARK}(?:'s|’s){MARK}\s+)?"
    claim = re.compile(
        rf"(?<![\w-]){possessive}{MARK}(?P<mon>{species}){MARK}\s+(?:\w+\s+){{0,2}}?{_USED}\s+{MARK}(?P<move>{moves}){MARK}"
        rf"(?:\s+(?:on|against|into|at|targeting)\s+(?:{MARK}(?P<tgt_who>{holder}){MARK}(?:'s|’s){MARK}\s+)?"
        rf"{MARK}(?P<target>{species}))?(?![\w-])", re.I)
    by_turn: dict[int, list[BattleEvent]] = {}
    for event in events:
        by_turn.setdefault(event.turn, []).append(event)
    issues: list[str] = []
    previous: set[int] = set()
    for sentence in sentences(answer, list(owners)):
        numbers = {turn for _, _, turns in turn_groups(sentence) for turn in turns}
        current = previous if not numbers and _THIS_TURN.search(sentence) else numbers
        previous = numbers
        text = _INSTEAD.sub("", sentence)
        if len(current) != 1:
            continue
        (turn,) = current
        for match in claim.finditer(text):
            if _HYPOTHETICAL.search(clause_around(text, match.start(), match.end())):
                continue
            mon = canonical(match.group("mon"), owners)
            players = [who[match.group("who").casefold()]] if match.group("who") else sorted(owners.get(mon, frozenset()))
            move = match.group("move").casefold()
            same_turn = [e for e in by_turn.get(turn, []) if e.actor_player in players
                         and base.get((e.actor_player, e.actor), e.actor) == base.get(
                             (e.actor_player, mon), mon) and e.move.casefold() == move]
            if not same_turn:
                issues.append(_missing(match.group(0).strip(), turn, mon, move, players, labels, events, base))
                continue
            if match.group("target"):
                target = canonical(match.group("target"), owners)
                aimed = {(player, base.get((player, name), name)) for event in same_turn for player, name in _hit_species(event)}
                owners_of = ([who[match.group("tgt_who").casefold()]] if match.group("tgt_who")
                             else sorted(owners.get(target, frozenset())))
                if aimed and not any((player, base.get((player, target), target)) in aimed for player in owners_of):
                    seen = ", ".join(f"{labels[p]}'s {name}" for p, name in sorted(aimed))
                    issues.append(f'"{match.group(0).strip()}" (turn {turn}): the log shows {move.title()} aimed at {seen}, '
                                  f"not {target}.")
    return list(dict.fromkeys(issues))


def _missing(quote: str, turn: int, mon: str, move: str, players: list[str], labels: dict[str, str],
             events: list[BattleEvent], base: dict[tuple[str, str], str]) -> str:
    key = base.get((players[0], mon), mon) if len(players) == 1 else mon
    mine = [e for e in events if e.actor_player in players and base.get((e.actor_player, e.actor), e.actor) == key]
    when = sorted({e.turn for e in mine if e.move.casefold() == move})
    that_turn = [e.move for e in mine if e.turn == turn]
    owner = " or ".join(labels[p] for p in players)
    others = [labels[e.actor_player] for e in events if e.turn == turn and e.move.casefold() == move
              and e.actor_player not in players and base.get((e.actor_player, e.actor), e.actor) == key]
    fact = (f"it was {' and '.join(dict.fromkeys(others))}'s {mon} that used it on turn {turn}" if others else
            f"{owner}'s {mon} used {move.title()} on turn {', '.join(map(str, when))}" if when
            else f"{owner}'s {mon} never used {move.title()}")
    return f'"{quote}" (turn {turn}): the log shows {owner}\'s {mon} did not use {move.title()} on turn {turn}' \
           f'{" (it used " + ", ".join(that_turn) + ")" if that_turn else ""}; {fact}.'
