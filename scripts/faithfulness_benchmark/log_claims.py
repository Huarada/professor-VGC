"""Score the explanation's damage claims against what the LOG shows happened.

The original verifier (``verify.py``) checks a ``damage_range`` claim against
the pipeline's own projected ranges — the very numbers Condition A was given.
That measures faithfulness to the evidence, not correctness, and it judges
Condition B against assumptions B never saw. This verifier uses the observed
damage from the raw log (``observed_damage.py``) instead, the same external
truth for both conditions:

- ``correct``: the claimed range is consistent with an observed hit of that
  attacker/move/defender (within HP-rounding tolerance; for a KO, the claim
  must be at least the HP the target had);
- ``incorrect``: such a hit exists and the claim contradicts it;
- ``not_in_log``: no such hit happened in the game — a hypothetical or a
  misattributed figure. Reported separately, never counted as correct.

``circularity_matrix`` cross-tabulates both verifiers for the same claims: a
claim the old benchmark scored correct (it matches the projection) while the
log contradicts it is exactly the false confidence the old metric produced.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from scripts.faithfulness_benchmark.engine_calibration import DEFAULT_TOLERANCE_PP
from scripts.faithfulness_benchmark.ground_truth import _norm, resolve_species
from scripts.faithfulness_benchmark.models import AtomicClaim, ClaimVerdict
from scripts.faithfulness_benchmark.observed_damage import ObservedHit

LogVerdict = Literal["correct", "incorrect", "not_in_log", "unverifiable"]


@dataclass
class LogClaimVerdict:
    claim: AtomicClaim
    verdict: LogVerdict
    reason: str = ""


def _consistent(claim: AtomicClaim, hit: ObservedHit, tolerance: float) -> bool:
    lo, hi = sorted((claim.min_percent or 0.0, claim.max_percent or 0.0))
    if hit.fainted:
        return hi + tolerance >= hit.hp_before
    return lo - tolerance <= hit.damage <= hi + tolerance


def verify_damage_claim_against_log(
    claim: AtomicClaim,
    hits: list[ObservedHit],
    tolerance: float = DEFAULT_TOLERANCE_PP,
) -> LogClaimVerdict:
    """Judge one ``damage_range`` claim against the observed hits of one game."""
    if claim.claim_type != "damage_range":
        return LogClaimVerdict(claim, "unverifiable", "not a damage claim")
    if (
        claim.min_percent is None or claim.max_percent is None
        or not claim.attacker or not claim.defender or not claim.move
    ):
        return LogClaimVerdict(claim, "unverifiable", "incomplete damage claim")
    species = {_norm(h.attacker) for h in hits} | {_norm(h.defender) for h in hits}
    attacker = resolve_species(claim.attacker, species)
    defender = resolve_species(claim.defender, species)
    move = _norm(claim.move)
    matching = [
        h for h in hits
        if _norm(h.attacker) == attacker and _norm(h.defender) == defender and _norm(h.move) == move
    ]
    if not matching:
        return LogClaimVerdict(claim, "not_in_log", "no such hit happened in this game")
    if any(_consistent(claim, h, tolerance) for h in matching):
        return LogClaimVerdict(claim, "correct")
    observed = ", ".join(
        f"T{h.turn} {h.damage}%" + (" (KO)" if h.fainted else "") for h in matching
    )
    return LogClaimVerdict(
        claim, "incorrect",
        f"claimed {claim.min_percent}-{claim.max_percent}% vs observed {observed}",
    )


def circularity_matrix(
    projection_verdicts: list[ClaimVerdict], log_verdicts: list[LogClaimVerdict]
) -> dict[str, int]:
    """``"<projection verdict> -> <log verdict>"`` counts for the same claims."""
    pairs = Counter(
        f"{p.verdict} -> {lv.verdict}" for p, lv in zip(projection_verdicts, log_verdicts)
    )
    return dict(sorted(pairs.items()))
