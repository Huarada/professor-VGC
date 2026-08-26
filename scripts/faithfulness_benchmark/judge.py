"""Claim-extraction judge (the 3rd, separate LLM call).

Receives ONLY the prose answer text — never the ground truth, never the
question, never which condition produced it — and returns a list of
:class:`AtomicClaim`. Verification against ground truth happens entirely
afterward, in ``verify.py``, with no LLM involved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from src.domain.interfaces import LLMProvider
from src.domain.models import ChatMessage
from scripts.faithfulness_benchmark.models import AtomicClaim

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge_system.txt"
_JUDGE_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# The judge prompt tells the model never to extract a "forfeit" claim from an
# ordinary faint/KO-based loss, but gpt-4o-mini doesn't reliably honor that
# negative instruction (seen live: "Raichu fainting" alone was extracted as
# a forfeit claim). Rather than trust prompt compliance alone, apply the same
# principle this whole project is built on (push the guarantee into
# deterministic code): a "forfeit" claim only survives if the word itself
# actually appears in the text it was supposedly extracted from.
_FORFEIT_KEYWORDS = ("forfeit", "concede", "conceded", "resign", "resigned", "quit")


def _is_real_forfeit_mention(claim_dict: dict) -> bool:
    text = f"{claim_dict.get('raw_text', '')} {claim_dict.get('subject', '')}".lower()
    return any(kw in text for kw in _FORFEIT_KEYWORDS)


def _extract_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def extract_claims(llm: LLMProvider, answer_text: str) -> list[AtomicClaim]:
    """Call the judge LLM once on ``answer_text`` and return parsed claims.

    Any single malformed object is skipped (and counted, via a print, so a
    systematically bad judge run is visible) rather than failing the whole
    extraction — mirrors this project's own "one bad item never take down
    the batch" convention (e.g. matchup_evaluator skipping a None verdict).
    """
    raw = llm.complete(
        system=_JUDGE_SYSTEM,
        messages=[ChatMessage(role="user", content=answer_text)],
        temperature=0.0,
        json_mode=False,
    )
    items = _extract_json_array(raw)
    claims: list[AtomicClaim] = []
    skipped = 0
    dropped_forfeit = 0
    for item in items:
        if isinstance(item, dict) and item.get("claim_type") == "forfeit" and not _is_real_forfeit_mention(item):
            dropped_forfeit += 1
            continue
        try:
            claims.append(AtomicClaim.model_validate(item))
        except ValidationError:
            skipped += 1
    if skipped:
        print(f"  [judge] skipped {skipped} malformed claim object(s)")
    if dropped_forfeit:
        print(f"  [judge] dropped {dropped_forfeit} forfeit claim(s) with no actual forfeit wording (prompt-compliance guard)")
    return claims
