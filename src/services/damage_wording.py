"""Deterministic check that HP LEFT is not written as damage DEALT (ADR-082).

"Blizzard dealt 47% to Scovillain" when the hit left 47% (it dealt 53%) was the commonest wrong figure in BO1 answers.
A sentence with a damage verb and "N% to <Pokemon>" is compared with the logged hits on that Pokemon: it is flagged only when
N equals the HP the Pokemon had left after a hit and differs from the damage that hit dealt.
"""

from __future__ import annotations

import re

from src.domain.models import GameState
from src.services.faint_timing import sentences
from src.services.side_attribution import MARK, alternation, canonical, owners_by_species

_DAMAGE_VERB = re.compile(r"\b(?:deal\w*|dealt|inflict\w*|damag\w*|took|takes?|taking)\b", re.I)
_TOLERANCE = 1.5  # pp, rounding of the logged percents
_MIN_GAP = 3.0


def _percent(raw: str) -> float | None:
    """The numerator of a "77/100" HP; None for any other scale (absolute HP is not a percent)."""
    numerator, _, denominator = raw.split()[0].partition("/") if raw.strip() else ("", "", "")
    try:
        return float(numerator) if denominator == "100" else None
    except ValueError:
        return None


def remaining_hp_as_damage_issues(answer: str, game_state: GameState) -> list[str]:
    outcome = game_state.outcome
    owners = owners_by_species(game_state)
    if outcome is None or not owners or len(game_state.sides) != 2:
        return []
    labels = {side.player: side.player_name or side.player for side in game_state.sides}
    who = {name.casefold(): player for player, name in labels.items()} | {player: player for player in labels}
    # species -> [(player, hp left, damage dealt)] for every logged hit
    hits: dict[str, list[tuple[str, float, float]]] = {}
    for event in outcome.events:
        for hit in event.hits:
            before, after = _percent(hit.hp_before_raw), hit.hp_after
            if before is not None and after is not None:
                hits.setdefault(hit.species, []).append((hit.player, after, before - after))
    holder, species = alternation(list(who)), alternation(list(owners))
    claim = re.compile(
        rf"(?<![\w.])(?P<pct>\d+(?:\.\d+)?)\s*%\s+(?:damage\s+)?(?:to|on|against)\s+"
        rf"(?:{MARK}(?P<who>{holder}){MARK}(?:'s|’s){MARK}\s+)?{MARK}(?P<mon>{species})(?![\w-])", re.I)
    issues: list[str] = []
    for sentence in sentences(answer, list(owners)):
        if not _DAMAGE_VERB.search(sentence):
            continue
        for match in claim.finditer(sentence):
            mon = canonical(match.group("mon"), owners)
            player = who.get((match.group("who") or "").casefold())
            pct = float(match.group("pct"))
            logged = [(left, dealt) for owner, left, dealt in hits.get(mon, []) if player in (None, owner)]
            if any(abs(dealt - pct) <= _TOLERANCE for _, dealt in logged):
                continue
            for left, dealt in logged:
                if abs(left - pct) <= _TOLERANCE and abs(dealt - pct) >= _MIN_GAP:
                    issues.append(f'"{match.group(0)}": {pct:g}% is the HP {mon} had left after a hit that dealt '
                                  f"{dealt:g}%, not damage dealt.")
                    break
    return list(dict.fromkeys(issues))
