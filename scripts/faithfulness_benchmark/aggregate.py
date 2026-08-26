"""Shared claim-extraction and rate-aggregation helpers.

Factored out of `run.py` (the original A/B benchmark entrypoint) so
`run_orchestrator_comparison.py` (the N-way orchestrator comparison) can
reuse the exact same extraction/filtering/counting logic instead of a second,
drifting copy — both scripts must count a "correct damage_range claim" the
same way for their numbers to ever be comparable to each other.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.domain.interfaces import LLMProvider

from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.judge import extract_claims
from scripts.faithfulness_benchmark.models import AtomicClaim, ClaimVerdict
from scripts.faithfulness_benchmark.verify import (
    filter_ambiguous_damage_claims,
    filter_degenerate_claims,
)


def extract_and_filter(
    llm: LLMProvider, text: str, gt: GroundTruth, label: str
) -> list[AtomicClaim]:
    """extract_claims() followed by both deterministic post-extraction
    guards, with a print per dropped category so a run's console output
    stays a legible audit trail of what the judge got wrong and why it
    didn't count against either condition."""
    claims, dropped_trainer = filter_degenerate_claims(extract_claims(llm, text), gt)
    if dropped_trainer:
        print(f"  [filter] dropped {dropped_trainer} trainer-named claim(s) ({label})")
    claims, dropped_ambiguous = filter_ambiguous_damage_claims(claims)
    if dropped_ambiguous:
        print(f"  [filter] dropped {dropped_ambiguous} non-damage-dealt percent claim(s) ({label})")
    return claims


def counts(claims: Sequence[ClaimVerdict], claim_type: str | None = None) -> tuple[int, int, int]:
    """(correct, incorrect, unverifiable) counts, optionally restricted to
    one claim_type — damage_range is what this benchmark's statistics are
    actually built on; the aggregate over every type mixes categories with
    a real effect (damage_range) and categories with none (move_used,
    pokemon_played, winner, ... — see README's per-claim-type table), which
    is why the aggregate is reported as context, not the headline."""
    if claim_type is not None:
        claims = [c for c in claims if c.claim.claim_type == claim_type]
    correct = sum(1 for c in claims if c.verdict == "correct")
    incorrect = sum(1 for c in claims if c.verdict == "incorrect")
    unverifiable = sum(1 for c in claims if c.verdict == "unverifiable")
    return correct, incorrect, unverifiable


def rate_dict(correct: int, incorrect: int, unverifiable: int) -> dict[str, Any]:
    total = correct + incorrect + unverifiable
    verifiable = correct + incorrect
    return {
        "total_claims": total,
        "correct": correct,
        "incorrect": incorrect,
        "unverifiable": unverifiable,
        "strict_faithfulness_rate": (correct / total) if total else None,
        "lenient_faithfulness_rate": (correct / verifiable) if verifiable else None,
    }
