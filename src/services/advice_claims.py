"""Deterministic checks of BO1 advice against the engine's per-move evidence (ADR-086).

A human review found advice the evidence contradicts: a "guaranteed OHKO" for a move the engine computes as a 2HKO,
and switches into a Pokemon that would have been left at a sliver. These read the answer against ``turn_checks``:
a KO claim about a move and a target must match a KO text the engine computed for that pair (or a one-hit KO the log
shows), and a recommended switch must be one of the engine's ``decision_options``. Anything the evidence cannot
settle (no computed pair, no named Pokemon) is left alone.
"""

from __future__ import annotations

import re

from src.domain.models import DecisionKind, GameState, TurnCheck
from src.services.faint_timing import _forme_bases, clause_around, sentences, turn_groups
from src.services.side_attribution import MARK, alternation, canonical, owners_by_species

_KO_CLAIM = re.compile(r"(?P<guaranteed>guaranteed\s+(?:an?\s+)?)?(?:(?P<n>[1-4])HKO|(?P<o>OHKO|one[-\s]hit\s+KO))", re.I)
_KO_TEXT = re.compile(r"(?P<guaranteed>guaranteed)?[^,;]*?(?P<n>\d+|O)HKO", re.I)
_CHANCE = re.compile(r"\d+(?:\.\d+)?\s*%\s+chance", re.I)
_HEDGE = re.compile(r"\b(?:potential\w*|possibl\w*|risk\w*|chance|might|may|threat\w*)\b", re.I)
_ADVICE = re.compile(r"\b(?:should|could|would|consider|instead|better|recommend|try|might|ought|needs?\s+to|prefer|"
                     r"option|safer|wiser)\b", re.I)


def _hits(text: str) -> int:
    return 1 if text.upper() == "O" else int(text)


def _computed(checks: list[TurnCheck]) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """(move, target) -> (turn, KO text) for every KO text the engine computed for that pair, plus a one-hit KO the log
    shows. The turn matters: the same move is a 2HKO into a full-HP target and an OHKO later."""
    found: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for check in checks:
        for damage in check.damage_checks:
            key = (check.move.casefold(), damage.target)
            if damage.projected_ko_text:
                found.setdefault(key, []).append((check.turn, damage.projected_ko_text))
            before = damage.target_hp_before_percent
            if damage.actual_result.startswith("fainted") and before is not None and before >= 99.5:
                found.setdefault(key, []).append((check.turn, "guaranteed OHKO (it happened)"))
        for option in check.best_alternatives:
            if option.ko_chance_text:
                found.setdefault((option.move.casefold(), option.target), []).append((check.turn, option.ko_chance_text))
    return found


def _sentences_with_turns(answer: str, species: list[str]) -> list[tuple[str, int | None]]:
    """Each sentence with the one turn it is about: its own, or the previous sentence's of the same line or bullet."""
    found: list[tuple[str, int | None]] = []
    for line in answer.splitlines():
        turn: int | None = None
        for sentence in sentences(line, species):
            named = {number for _, _, group in turn_groups(sentence) for number in group}
            turn = next(iter(named)) if len(named) == 1 else (None if named else turn)
            found.append((sentence, turn))
    return found


def _supported(claim: re.Match[str], texts: list[str]) -> bool:
    wanted = 1 if claim.group("o") else int(claim.group("n"))
    for text in texts:
        computed = _KO_TEXT.search(text)
        if computed is None:
            continue
        if _hits(computed.group("n")) <= wanted and (computed.group("guaranteed") or not claim.group("guaranteed")):
            return True
    return False


def ko_claim_issues(answer: str, game_state: GameState, checks: list[TurnCheck]) -> list[str]:
    """A KO claim ("guaranteed OHKO", "2HKO") about a named move and target that no engine text for that pair supports."""
    owners = owners_by_species(game_state)
    computed = _computed(checks)
    if not owners or not computed:
        return []
    base = {forme: species for (_, forme), species in _forme_bases(game_state).items()}
    move_name = re.compile(rf"(?<![\w-])(?:{alternation(sorted({move for move, _ in computed}))})(?![\w-])", re.I)
    pokemon = re.compile(rf"(?<![\w-])(?:{alternation(list(owners))})(?![\w-])", re.I)
    issues: list[str] = []
    for sentence, turn in _sentences_with_turns(answer, list(owners)):
        for claim in _KO_CLAIM.finditer(sentence):
            clause = clause_around(sentence, claim.start(), claim.end())
            if _CHANCE.search(clause) or (not claim.group("guaranteed") and _HEDGE.search(clause)):
                continue  # "a 31% chance to OHKO" or "risked a potential OHKO" is not a promise the engine can refute
            # The claim is about the last move named before it and the Pokemon named closest before the claim.
            before = sentence[:claim.start()]
            named = list(move_name.finditer(before))
            if not named:
                continue
            after_move = list(pokemon.finditer(before, named[-1].end()))
            target = after_move[-1] if after_move else None
            if target is None:  # "its vulnerability to Basculegion's Wave Crash": the possessive is the attacker
                earlier = [found for found in pokemon.finditer(before, 0, named[-1].start())
                           if not re.match(r"[*_`]*['’]s\b", before[found.end():])]
                target = earlier[-1] if earlier else None
            if target is None:
                continue
            move, mon = named[-1].group(0).casefold(), canonical(target.group(0), owners)
            pairs = computed.get((move, mon)) or computed.get((move, base.get(mon, mon))) or []
            texts = [text for when, text in pairs if turn is None or when == turn]
            if not texts or _supported(claim, texts):
                continue
            where = f" on turn {turn}" if turn is not None else ""
            shown = "; ".join(dict.fromkeys(texts))
            issues.append(f'"{claim.group(0)}" ({move.title()} into {mon}{where}): the engine computed only "{shown}" for '
                          f"that move and target; do not claim a stronger KO.")
    return list(dict.fromkeys(issues))


def _team_names(game_state: GameState) -> dict[str, frozenset[str]]:
    """Every Pokemon on both team sheets (advice may name one that never entered the game) -> who brought it."""
    names: dict[str, frozenset[str]] = {name: frozenset() for side in game_state.sides for mon in side.team
                                        for name in [mon.species, *mon.battle_formes]}
    return names | owners_by_species(game_state)


def switch_advice_issues(answer: str, game_state: GameState, checks: list[TurnCheck]) -> list[str]:
    """Advice to switch into a Pokemon that is not one of the engine's switch options (on the named turn, if any)."""
    owners = _team_names(game_state)
    if not owners:
        return []
    listed = [(check.turn, check.actor, option.switch_to) for check in checks for option in check.decision_options
              if option.kind == DecisionKind.SWITCH]
    species = alternation(list(owners))
    labels = alternation([side.player_name or side.player for side in game_state.sides])
    owner = rf"(?:{MARK}(?:{labels}){MARK}(?:'s|’s){MARK}\s+)?"
    advice = re.compile(
        rf"\b(?P<verb>switch\w*)\s+(?:out\s+)?{owner}{MARK}(?P<out>{species}){MARK}\s+(?:out\s+)?(?:to|into|for|with)\s+"
        rf"{owner}{MARK}(?P<into>{species})(?![\w-])"
        rf"|\b(?P<verb2>switch\w*|bring\w*)\s+(?:in(?:to)?|to)\s+{owner}{MARK}(?P<only>{species})(?![\w-])", re.I)
    issues: list[str] = []
    for sentence in sentences(answer, list(owners)):
        turns = {turn for _, _, group in turn_groups(sentence) for turn in group}
        for match in advice.finditer(sentence):
            verb = (match.group("verb") or match.group("verb2")).casefold()
            narrated = verb in {"switched", "brought"} and not re.search(rf"\bhave\s+{verb}", sentence, re.I)
            if narrated or not _ADVICE.search(sentence):
                continue  # a switch that happened is narration, not advice
            into = canonical(match.group("into") or match.group("only"), owners)
            out = canonical(match.group("out"), owners) if match.group("out") else None
            fits = [entry for entry in listed if entry[2] == into and (out is None or entry[1] == out)
                    and (len(turns) != 1 or entry[0] in turns)]
            if fits:
                continue
            where = f" on turn {next(iter(turns))}" if len(turns) == 1 else ""
            source = f" from {out}" if out else ""
            issues.append(f'"{match.group(0)}": the engine lists no safe switch{source} into {into}{where} (a listed switch '
                          "keeps more than half its HP against both opponents). Recommend only switches in decision_options.")
    return list(dict.fromkeys(issues))


_SELF_PROTECT = frozenset({"protect", "detect", "spikyshield", "banefulbunker", "kingsshield", "silktrap",
                           "burningbulwark", "obstruct"})
# Who the advice is NOT about: an attacker ("from Dragalge"), a switch-in, a suggested lead.
_NOT_THE_SUBJECT = re.compile(r"(?:\b(?:from|by|of)\s+(?:\S+\s+)?|\b(?:switch\w*|swap\w*|bring\w*|go(?:ing)?)\s+(?:\S+\s+){0,3}?"
                              r"(?:to|into|for|in|with)\s+(?:\S+\s+)?|\blead\w*\s+(?:with\s+)?(?:\S+\s+)?|\bwith\s+)$", re.I)
_ADVISED_PROTECT = re.compile(r"\b(?:could|should|would|might|ought\s+to)\s+(?:have\s+)?(?:\w+\s+){0,2}?(?:used\s+|use\s+)?"
                              r"(?:protect|detect)\b|\b(?:protect|detect)\s+(?:\w+\s+){0,3}?(?:should|could|would)\b", re.I)
_ADVISED_TARGET = re.compile(r"\b(?:could|should|would|might|consider\w*|better)\b[^.;]{0,30}\b(?:target\w*|attack\w*|"
                             r"against|into|at|on)\s+(?:\S+\s+)?$", re.I)


def _turn_facts(game_state: GameState) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    """turn -> species on the field at some point of it, and turn -> species behind their own Protect that turn."""
    present: dict[int, set[str]] = {}
    shielded: dict[int, set[str]] = {}
    outcome = game_state.outcome
    for event in outcome.events if outcome else []:
        if event.kind == "switch":
            present.setdefault(event.turn, set()).add(event.actor)
        if event.kind != "move":
            continue
        present.setdefault(event.turn, set()).add(event.actor)
        if event.state is not None:
            present[event.turn] |= {name for names in event.state.active.values() for name in names}
        if re.sub(r"[^a-z]", "", event.move.lower()) in _SELF_PROTECT and "failed" not in event.effects:
            shielded.setdefault(event.turn, set()).add(event.actor)
        shielded.setdefault(event.turn, set()).update(
            hit.species for hit in event.hits if re.sub(r"[^a-z]", "", hit.blocked_by.lower()) in _SELF_PROTECT)
    return present, shielded


def presence_issues(answer: str, game_state: GameState) -> list[str]:
    """Advice about turn N for a Pokemon that could not follow it then: not on the field, told to Protect when it did
    Protect, or told to attack a target that was behind Protect. Only a sentence that names its own turn is read, and a
    switch-in or lead suggestion is never a presence claim (a bench Pokemon is off the field by definition)."""
    owners = _team_names(game_state)
    if not owners or game_state.outcome is None:
        return []
    base = {forme: species for (_, forme), species in _forme_bases(game_state).items()}
    present, shielded = _turn_facts(game_state)
    pokemon = re.compile(rf"(?<![\w-]){MARK}(?P<mon>{alternation(list(owners))})(?![\w-])", re.I)
    issues: list[str] = []
    for sentence in sentences(answer, list(owners)):
        turns = {turn for _, _, group in turn_groups(sentence) for turn in group}
        if len(turns) != 1 or not _ADVICE.search(sentence):
            continue
        (turn,) = turns
        if turn not in present:
            continue
        on_field = {base.get(name, name) for name in present[turn]} | present[turn]
        behind = {base.get(name, name) for name in shielded.get(turn, set())} | shielded.get(turn, set())
        for match in pokemon.finditer(sentence):
            clause = clause_around(sentence, match.start(), match.end())
            mon = canonical(match.group("mon"), owners)
            before = sentence[max(0, match.start() - 40):match.start()].lower()
            if re.match(r"[*_`]*['’]s\b", sentence[match.end():match.end() + 4]) or _NOT_THE_SUBJECT.search(before):
                continue
            if mon not in on_field and base.get(mon, mon) not in on_field:
                if _ADVICE.search(clause):
                    issues.append(f'"{match.group(0).strip("*_` ")}" (turn {turn}): it was not on the field on turn {turn}, '
                                  "so advice for that turn cannot involve it.")
                continue
            if mon not in behind and base.get(mon, mon) not in behind:
                continue
            if _ADVISED_PROTECT.search(clause) and not re.search(r"\binstead\s+of\b", clause, re.I):
                issues.append(f'"{clause.strip()}" (turn {turn}): {mon} did use Protect on turn {turn}.')
            elif _ADVISED_TARGET.search(before) and not re.search(r"\b(?:protect|detect)[*_`]*\s+on\s+(?:\S+\s+)?$", before):
                issues.append(f'"{clause.strip()}" (turn {turn}): {mon} was behind Protect on turn {turn}, so an attack '
                              "into it would have done nothing.")
    return list(dict.fromkeys(issues))


def advice_issues(answer: str, game_state: GameState, checks: list[TurnCheck]) -> list[str]:
    return (ko_claim_issues(answer, game_state, checks) + switch_advice_issues(answer, game_state, checks)
            + presence_issues(answer, game_state))
