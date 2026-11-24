"""Does the answer present the engine's turning point as THE turning point?

The prompt asks the model to narrate `turning_point`, and some answers still cite the engine's turn only in passing
while calling another turn decisive. A sentence that calls a turn the turning point (or decisive, pivotal) is read
for the turns it names; the answer passes when any such sentence names the engine's turn, so a secondary swing
("another decisive moment on turn 4") is allowed. An answer with no such claim is not flagged.
"""

from __future__ import annotations

import re

from src.domain.models import GameState
from src.services.faint_timing import sentences, turn_groups
from src.services.side_attribution import owners_by_species
from src.services.turning_point import find_turning_point

_MARKER = re.compile(r"turning[\s-]*point|\b(?:decisive|pivotal)\b|decided\s+the\s+(?:game|match|battle)|deciding\s+(?:moment|turn)",
                     re.I)
_HEADING_WORDS = 6  # a bare "### Turning Point" line takes its turn from the sentence that follows


def _nearest_turns(sentence: str, marker: re.Match[str]) -> set[int]:
    """The turns named closest to the marker (the one after it wins a tie), so "the turning point was turn 5; turn 11
    only closed it out" claims turn 5."""
    def distance(group: tuple[int, int, set[int]]) -> tuple[int, int]:
        start, end, _ = group
        return (start - marker.end(), 0) if start >= marker.end() else (marker.start() - end, 1)

    groups = turn_groups(sentence)
    return min(groups, key=distance)[2] if groups else set()


def claimed_turning_turns(answer: str, species: list[str]) -> list[set[int]]:
    """For each sentence that calls something the turning point, the turn it names (or its heading's next line)."""
    parts = sentences(answer, species)
    claims: list[set[int]] = []
    for index, sentence in enumerate(parts):
        marker = _MARKER.search(sentence)
        if marker is None:
            continue
        named = _nearest_turns(sentence, marker)
        short_heading = len(re.findall(r"\w+", sentence)) <= _HEADING_WORDS
        if not named and short_heading and index + 1 < len(parts):
            following = turn_groups(parts[index + 1])
            named = following[0][2] if following else set()
        if named:
            claims.append(named)
    return claims


def turning_point_issues(answer: str, game_state: GameState) -> list[str]:
    point = find_turning_point(game_state)
    if point is None:
        return []
    claims = claimed_turning_turns(answer, list(owners_by_species(game_state)))
    if not claims or any(point.turn in named for named in claims):
        return []
    named = sorted({turn for turns in claims for turn in turns})
    return [f"The answer calls turn {', '.join(map(str, named))} the turning point, but the engine computed turn "
            f"{point.turn}: {point.summary}"]
