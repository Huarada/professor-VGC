"""Deterministic classifier: does a percent-bearing phrase describe damage
DEALT, or one of three OTHER percent-bearing facts a free-text extractor can
mistake for it — HP remaining after a hit, a KO-chance probability, or a
bare raw-damage-points figure with no percent at all?

Deliberately grounded in THIS PROJECT'S OWN fixed, machine-generated
vocabulary — never in this benchmark's specific observed sentences, so the
rules generalize to any real ProfessorVGC output instead of memorizing ten
fixtures:

  - KO-chance text is @smogon/calc's own literal output convention
    ("guaranteed OHKO", "12.5% chance to OHKO", "possible 7HKO") — see
    node_calc/src/calcEngine.js / DamageResult.ko_chance_text. This exact
    phrasing is a stable format the calc engine itself emits, not a
    guess at how an LLM might paraphrase something.
  - "ended at N% HP" is TurnReplaySimulator._actual_results()'s own literal
    phrasing for remaining HP (src/services/turn_simulator.py) — the
    explanation prompt places this string directly in context, so a
    faithful paraphrase tends to preserve its shape ("left/leaving/down to
    N% HP", etc.) even after rewording around it.

Why a classifier and not just a keyword scan of the WHOLE sentence: a
single sentence can legitimately combine a genuine damage-dealt percent
with an unrelated KO-chance mention right next to it (e.g. "dealt 85.3-
101.3% damage — a guaranteed OHKO"). Classifying the small window of text
immediately around the SPECIFIC claimed number, rather than the sentence
as a whole, is what keeps that case correctly recognized as damage-dealt.
"""

from __future__ import annotations

import re
from typing import Literal

PercentKind = Literal["damage_dealt", "hp_remaining", "ko_chance", "raw_points"]

_KO_CHANCE_RE = re.compile(
    r"chance\s+to\s+\w*hko|guaranteed\s+\w*hko|possible\s+\w*hko"
)
_HP_REMAINING_RE = re.compile(
    r"(left|leav\w*|end\w*|remain\w*|down\s+to|surviv\w*\s+with)\D{0,25}"
    r"\d+(\.\d+)?\s*%\s*hp"
)
# A narrower companion for the same "leaving X at Y%" battle-report turn of
# phrase WITHOUT the word "HP" attached (e.g. "leaving Staraptor at 64%") —
# tighter (requires the literal word "at" right before the number) so it
# doesn't also catch an unrelated "leaving"/"remain" elsewhere in a sentence
# that states a genuine damage-dealt percent.
_HP_REMAINING_BARE_RE = re.compile(r"\b(leav\w*|left|remain\w*)\b[^%]{0,20}\bat\s+\d+(\.\d+)?%")
_RAW_POINTS_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(-|and)\s*\d+(\.\d+)?\s+damage\b"
)

_WINDOW_RADIUS = 35


def _number_window(text: str, value: float) -> str:
    """The small span of text immediately around wherever `value` is
    mentioned — not the whole sentence, which may separately describe an
    unrelated fact (a KO chance, a different Pokemon's remaining HP) that
    would otherwise cause a false match."""
    lowered = text.lower()
    candidates: list[str] = [f"{value:.1f}"]
    if value == int(value):
        candidates.append(str(int(value)))
    candidates.append(str(value))
    for rep in candidates:
        idx = lowered.find(rep.lower())
        if idx != -1:
            start = max(0, idx - _WINDOW_RADIUS)
            end = min(len(lowered), idx + len(rep) + _WINDOW_RADIUS)
            return lowered[start:end]
    return lowered  # the exact number string wasn't found — fall back to the whole text


def classify_percent_mention(raw_text: str, value: float) -> PercentKind:
    """Classify what a specific claimed percent VALUE actually represents,
    by inspecting only the text immediately around that value in `raw_text`."""
    window = _number_window(raw_text, value)
    if _KO_CHANCE_RE.search(window):
        return "ko_chance"
    if _HP_REMAINING_RE.search(window) or _HP_REMAINING_BARE_RE.search(window):
        return "hp_remaining"
    if _RAW_POINTS_RE.search(window) and "%" not in window:
        return "raw_points"
    return "damage_dealt"


def is_genuine_damage_dealt_claim(raw_text: str, value: float) -> bool:
    return classify_percent_mention(raw_text, value) == "damage_dealt"
