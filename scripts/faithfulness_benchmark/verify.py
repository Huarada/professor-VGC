"""Deterministic claim verification — the whole point of this benchmark.

No LLM call anywhere in this file. Every ``verify_claim`` branch is a plain
comparison against :class:`~scripts.faithfulness_benchmark.ground_truth.GroundTruth`.
"""

from __future__ import annotations

from scripts.faithfulness_benchmark.ground_truth import (
    GroundTruth,
    _matches_side,
    _norm,
    _norm_forme,
    resolve_species,
)
from scripts.faithfulness_benchmark.models import AtomicClaim, ClaimVerdict

_DAMAGE_TOLERANCE_PP = 3.0  # percentage points

_PLAYER_NAMED_TYPES = {"pokemon_played", "move_used"}


def filter_degenerate_claims(claims: list[AtomicClaim], gt: GroundTruth) -> tuple[list[AtomicClaim], int]:
    """Drop claims that name the TRAINER, not a Pokemon.

    The judge prompt asks for one claim per entity, but gpt-4o-mini
    sometimes collapses "Ash used Torkoal and Flutter Mane" into a single
    pokemon_played claim about "Ash" instead of one each for Torkoal and
    Flutter Mane — a judge extraction artifact, not a fact about the game's
    Pokemon. Scoring it as "incorrect" (Ash genuinely isn't a Pokemon) would
    be technically defensible but would penalize a condition for the judge's
    mistake, not its own content — so these are excluded from both
    conditions' counts entirely, same principle as the forfeit-keyword guard
    in judge.py. Returns (kept_claims, dropped_count).
    """
    kept: list[AtomicClaim] = []
    dropped = 0
    for c in claims:
        if (
            c.claim_type in _PLAYER_NAMED_TYPES
            and c.subject
            and resolve_species(c.subject, gt.real_species) is None
            and (_matches_side(c.subject, gt.winner_aliases) or _matches_side(c.subject, gt.loser_aliases))
        ):
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


def filter_ambiguous_damage_claims(claims: list[AtomicClaim]) -> tuple[list[AtomicClaim], int]:
    """Drop damage_range claims whose raw_text actually describes a
    KO-chance probability, remaining HP, or bare raw damage points — not
    damage dealt at all — per the deterministic rules in
    percent_classifier.py (grounded in this project's own fixed
    vocabulary, not memorized benchmark phrasings; see that module's own
    docstring). Same principle as filter_degenerate_claims: excluded from
    both conditions' counts entirely rather than scored, since the mistake
    being corrected is the judge's extraction, not the answer's content.
    Returns (kept_claims, dropped_count).
    """
    from scripts.faithfulness_benchmark.percent_classifier import is_genuine_damage_dealt_claim

    kept: list[AtomicClaim] = []
    dropped = 0
    for c in claims:
        if (
            c.claim_type == "damage_range"
            and c.raw_text
            and c.min_percent is not None
            and c.max_percent is not None
        ):
            genuine = is_genuine_damage_dealt_claim(
                c.raw_text, c.min_percent
            ) and is_genuine_damage_dealt_claim(c.raw_text, c.max_percent)
            if not genuine:
                dropped += 1
                continue
        kept.append(c)
    return kept, dropped


def _ranges_overlap(a: tuple[float, float], b: tuple[float, float], tol: float) -> bool:
    a_lo, a_hi = a[0] - tol, a[1] + tol
    b_lo, b_hi = min(b), max(b)
    return a_lo <= b_hi and b_lo <= a_hi


def verify_claim(claim: AtomicClaim, gt: GroundTruth) -> ClaimVerdict:
    if claim.claim_type == "move_used":
        move = _norm(claim.move)
        if not claim.subject or not move:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing subject/move")
        species = resolve_species(claim.subject, gt.real_species)
        if species is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject!r} is not a real Pokemon in this game")
        if move in gt.moves_used.get(species, set()):
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(
            claim=claim, verdict="incorrect",
            reason=f"{claim.subject} never used {claim.move} (real moves: {sorted(gt.moves_used.get(species, set()))})",
        )

    if claim.claim_type == "pokemon_played":
        if not claim.subject:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing subject")
        species = resolve_species(claim.subject, gt.real_species)
        if species is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject!r} is not a real Pokemon in this game")
        if species in gt.in_play:
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject} was team-previewed but never brought in (benched)")

    if claim.claim_type == "damage_range":
        move = _norm(claim.move)
        if claim.min_percent is None or claim.max_percent is None or not claim.attacker or not claim.defender or not move:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="incomplete damage claim")
        attacker = resolve_species(claim.attacker, gt.real_species)
        defender = resolve_species(claim.defender, gt.real_species)
        if attacker is None or defender is None:
            bad = claim.attacker if attacker is None else claim.defender
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{bad!r} is not a real Pokemon in this game")
        real_ranges = gt.damage_ranges.get((attacker, defender, move))
        if real_ranges is None:
            # No precomputed verdict/turn-check exists for this exact triple.
            # If the move was never even confirmed for this attacker, the
            # claim is a fabrication regardless of the number attached; if it
            # WAS confirmed, we simply have no computed figure to check the
            # number against (may be a legitimate off-script hypothetical).
            if move not in gt.moves_used.get(attacker, set()):
                return ClaimVerdict(
                    claim=claim, verdict="incorrect",
                    reason=f"{claim.attacker} never used {claim.move} in this game, so this damage figure cannot be real",
                )
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="no ground-truth verdict computed for this exact matchup/move")
        claimed = (claim.min_percent, claim.max_percent)
        if any(_ranges_overlap(claimed, real, _DAMAGE_TOLERANCE_PP) for real in real_ranges):
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(
            claim=claim, verdict="incorrect",
            reason=f"claimed {claim.min_percent}-{claim.max_percent}% vs real {real_ranges}",
        )

    if claim.claim_type == "forme_change":
        forme = _norm_forme(claim.forme)
        if not claim.subject or not forme:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing subject/forme")
        species = resolve_species(claim.subject, gt.real_species)
        if species is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject!r} is not a real Pokemon in this game")
        real_formes = gt.forme_changes.get(species, set())
        if any(forme in rf or rf in forme for rf in real_formes):
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject} was never observed transforming into {claim.forme}")

    if claim.claim_type == "stat_stage":
        stat = claim.stat.lower().strip()
        if not claim.subject or not stat:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing subject/stat")
        species = resolve_species(claim.subject, gt.real_species)
        if species is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject!r} is not a real Pokemon in this game")
        direction_sign = None
        if claim.stages is not None and claim.stages != 0:
            direction_sign = 1 if claim.stages > 0 else -1
        matches = [d for (sp, st, d) in gt.boost_deltas if sp == species and st == stat]
        if not matches:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"no recorded {claim.stat} change for {claim.subject}")
        if direction_sign is None:
            return ClaimVerdict(claim=claim, verdict="correct")  # direction-only claim already matched a real change
        if any((1 if d > 0 else -1) == direction_sign for d in matches):
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"real change(s) for {claim.subject} {claim.stat}: {matches}")

    if claim.claim_type == "protect_block":
        move = _norm(claim.blocked_move)
        if not claim.subject or not move:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing subject/blocked_move")
        blocker = resolve_species(claim.subject, gt.real_species)
        if blocker is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject!r} is not a real Pokemon in this game")
        if (blocker, move) in gt.blocked_pairs:
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"{claim.subject} never blocked {claim.blocked_move} with Protect")

    if claim.claim_type == "winner":
        if not claim.subject and not claim.move:
            return ClaimVerdict(claim=claim, verdict="unverifiable", reason="missing winner name")
        claimed_name = claim.subject or claim.move
        matches_winner = _matches_side(claimed_name, gt.winner_aliases)
        matches_loser = _matches_side(claimed_name, gt.loser_aliases)
        if matches_winner and not matches_loser:
            return ClaimVerdict(claim=claim, verdict="correct")
        if matches_loser and not matches_winner:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"real winner is {gt.winner!r}, claim names the LOSING side {claimed_name!r}")
        return ClaimVerdict(claim=claim, verdict="unverifiable", reason=f"could not confidently match {claimed_name!r} to a side")

    if claim.claim_type == "forfeit":
        if gt.forfeited is None:
            return ClaimVerdict(claim=claim, verdict="incorrect", reason="this game was NOT decided by forfeit")
        if not claim.subject or _matches_side(claim.subject, gt.forfeited_aliases):
            return ClaimVerdict(claim=claim, verdict="correct")
        return ClaimVerdict(claim=claim, verdict="incorrect", reason=f"real forfeiting side was {gt.forfeited!r}")

    return ClaimVerdict(claim=claim, verdict="unverifiable", reason=f"unhandled claim_type {claim.claim_type!r}")
