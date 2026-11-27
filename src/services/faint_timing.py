"""Deterministic check of "Pokemon X fainted on turn N" claims against the log's faint list.

gpt-4o-mini placed a turn-12 KO inside turn 11 while explaining the turning point. Only sentences that name exactly
one turn and a clear victim ("fainted", "a KO of Player A's Dragonite", "knocked out Klefki") are checked; anything
less explicit is left alone, so a missed claim is possible but a wrong flag should not be.
"""

from __future__ import annotations

import re

from src.domain.models import GameState
from src.services.side_attribution import MARK, alternation, canonical, owners_by_species

_TURN = re.compile(r"(?<![\w-])turns?\s+(\d+(?:\s*(?:,|and|&|-|–|to)\s*\d+)*)(?![\w-])", re.I)
_VERBS = (r"(?<![A-Za-z])(?:KO(?:'d|’d|s|ing)?|knock(?:s|ed|ing)?\s+out|faint(?:s|ed|ing)?|defeat(?:s|ed|ing)?|"
          r"(?:took|takes?|taking)\s+out|eliminat(?:es|ed|ing))")
# Counterfactuals ("would have KO'd", "guaranteed OHKO") describe alternatives, not what happened.
_HYPOTHETICAL = re.compile(
    r"\b(?:would|could|might|may|should|can|if|instead|guaranteed|chance|threat|potential|possible|likely)\b", re.I)
_CLAUSE_END = re.compile(r"[,;:()—–]|\s-\s")
_THIS_TURN = re.compile(r"\b(?:this|that|the same)\s+(?:\w+\s+)?turn\b", re.I)
_MASK = "․"  # one-dot leader: keeps "Mr. Rime" from being split as a sentence end


def _forme_bases(game_state: GameState) -> dict[tuple[str, str], str]:
    """(player, forme) -> the species the side brought, so a Mega shares its base Pokemon's faint."""
    return {(side.player, forme): mon.species
            for side in game_state.sides for mon in side.team for forme in mon.battle_formes}


def _faint_turns(game_state: GameState, base: dict[tuple[str, str], str]) -> dict[tuple[str, str], set[int]]:
    """(player, species) -> the turns it fainted."""
    turns: dict[tuple[str, str], set[int]] = {}
    if game_state.outcome is None:
        return turns
    for ko in game_state.outcome.kos:
        turns.setdefault((ko.player, base.get((ko.player, ko.fainted), ko.fainted)), set()).add(ko.turn)
    return turns


def clause_around(sentence: str, start: int, end: int) -> str:
    """The clause holding sentence[start:end]: a counterfactual word elsewhere in the sentence ("...decreasing their
    offensive potential") must not hide a factual claim from the checks (ADR-084)."""
    left = max((m.end() for m in _CLAUSE_END.finditer(sentence, 0, start)), default=0)
    right = next((m.start() for m in _CLAUSE_END.finditer(sentence, end)), len(sentence))
    return sentence[left:right]


def turn_groups(sentence: str) -> list[tuple[int, int, set[int]]]:
    """(start, end, turns) of every "turn 5" / "turns 5 and 6" in a sentence."""
    return [(m.start(), m.end(), {int(n) for n in re.findall(r"\d+", m.group(1))}) for m in _TURN.finditer(sentence)]


def turn_numbers(sentence: str) -> set[int]:
    """The turn numbers a sentence names ("turn 5", "turns 5 and 6")."""
    return {turn for _, _, turns in turn_groups(sentence) for turn in turns}


def sentences(text: str, species: list[str]) -> list[str]:
    for name in species:
        if "." in name:
            text = text.replace(name, name.replace(".", _MASK))
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.replace(_MASK, ".") for part in parts if part.strip()]


def faint_timing_issues(answer: str, game_state: GameState) -> list[str]:
    outcome = game_state.outcome
    owners = owners_by_species(game_state)
    if outcome is None or not outcome.kos or not owners or len(game_state.sides) != 2:
        return []
    labels = {side.player: side.player_name or side.player for side in game_state.sides}
    who = {name.casefold(): player for player, name in labels.items()} | {player: player for player in labels}
    holder, species = alternation(list(who)), alternation(list(owners))
    possessive = rf"(?:\*{{0,2}}(?P<who>{holder})\*{{0,2}}(?:'s|’s)\*{{0,2}}\s+)?"
    after = re.compile(rf"{_VERBS}\s+(?:of\s+)?{MARK}{possessive}{MARK}(?P<mon>{species})(?![\w-])", re.I)
    before = re.compile(rf"(?<![\w-]){possessive}{MARK}(?P<mon>{species}){MARK}\s+(?:\w+\s+)?(?:fainted|was\s+knocked\s+out|was\s+KO(?:'|’)?d)", re.I)
    # "... on Dragonite, which had 2% left, causing a faint": the relative clause belongs to the victim.
    clause = re.compile(rf"(?<![\w-]){possessive}{MARK}(?P<mon>{species}){MARK},?\s+(?:which|who|that)\b[^.;!?]{{0,80}}?"
                        r"(?:caus|result|lead)\w*\s+(?:in\s+|to\s+)?(?:a\s+|its\s+|the\s+)?(?:faint|KO)", re.I)
    base = _forme_bases(game_state)
    faints = _faint_turns(game_state, base)
    issues: list[str] = []
    previous: set[int] = set()
    for sentence in sentences(answer, list(owners)):
        numbers = turn_numbers(sentence)
        # "During this turn, ..." continues the turn named by the sentence right before it.
        inherited = not numbers and bool(_THIS_TURN.search(sentence))
        current = previous if inherited else numbers
        previous = numbers
        if len(current) != 1:
            continue
        (turn,) = current
        for pattern in (after, before, clause):
            for match in pattern.finditer(sentence):
                if _HYPOTHETICAL.search(clause_around(sentence, match.start(), match.end())):
                    continue
                mon = canonical(match.group("mon"), owners)
                claimed = who.get((match.groupdict().get("who") or "").casefold())
                players = [claimed] if claimed else sorted(owners.get(mon, frozenset()))
                seen = sorted({t for p in players for t in faints.get((p, base.get((p, mon), mon)), set())})
                if turn in seen:
                    continue
                where = f"on turn {', '.join(map(str, seen))}" if seen else "at all in the log"
                issues.append(f'"{match.group(0)}" (turn {turn}): {mon} fainted {where}.')
    return list(dict.fromkeys(issues))
