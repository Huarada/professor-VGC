"""Unit tests for the faithfulness benchmark's deterministic verifier.

No LLM, no network — this only tests scripts/faithfulness_benchmark/verify.py
against hand-built GroundTruth objects, mirroring this project's own
"verify the deterministic layer with fakes, no LLM required" convention.
"""

from __future__ import annotations

from scripts.faithfulness_benchmark.ground_truth import GroundTruth
from scripts.faithfulness_benchmark.models import AtomicClaim
from scripts.faithfulness_benchmark.verify import filter_degenerate_claims, verify_claim


def _gt(**kwargs) -> GroundTruth:
    return GroundTruth(game_state=None, analysis=None, **kwargs)  # type: ignore[arg-type]


def test_move_used_correct():
    gt = _gt(real_species={"garchomp"}, moves_used={"garchomp": {"earthquake"}})
    claim = AtomicClaim(claim_type="move_used", subject="Garchomp", move="Earthquake")
    assert verify_claim(claim, gt).verdict == "correct"


def test_move_used_fabricated_move_is_incorrect():
    gt = _gt(real_species={"garchomp"}, moves_used={"garchomp": {"earthquake"}})
    claim = AtomicClaim(claim_type="move_used", subject="Garchomp", move="Tackle")
    v = verify_claim(claim, gt)
    assert v.verdict == "incorrect"


def test_move_used_resolves_species_through_the_side_prefix_the_real_prompt_requires():
    """The production explanation_system.txt REQUIRES every mention to carry
    its p1/p2 prefix (CLAUDE.md's anti-misattribution rule) — a faithful
    grounded answer routinely says "p2 Garchomp" or "Gengar (p2)" rather than
    a bare species name. Regression: this must resolve to the real species,
    not be flagged as a hallucinated Pokemon."""
    gt = _gt(real_species={"garchomp"}, moves_used={"garchomp": {"earthquake"}})
    for phrasing in ["p2 Garchomp", "Garchomp (p2)", "Ash's Garchomp"]:
        claim = AtomicClaim(claim_type="move_used", subject=phrasing, move="Earthquake")
        assert verify_claim(claim, gt).verdict == "correct", phrasing


def test_move_used_hallucinated_species_is_incorrect():
    gt = _gt(real_species={"garchomp"}, moves_used={"garchomp": {"earthquake"}})
    claim = AtomicClaim(claim_type="move_used", subject="Mewtwo", move="Psystrike")
    assert verify_claim(claim, gt).verdict == "incorrect"


def test_pokemon_played_benched_is_incorrect():
    gt = _gt(real_species={"raichu", "whimsicott", "garchomp"}, in_play={"raichu", "garchomp"})
    claim = AtomicClaim(claim_type="pokemon_played", subject="Whimsicott")
    v = verify_claim(claim, gt)
    assert v.verdict == "incorrect"
    assert "benched" in v.reason


def test_pokemon_played_correct_when_actually_brought():
    gt = _gt(real_species={"raichu", "garchomp"}, in_play={"raichu", "garchomp"})
    claim = AtomicClaim(claim_type="pokemon_played", subject="Raichu")
    assert verify_claim(claim, gt).verdict == "correct"


_GARCHOMP_SINISTCHA = {"garchomp", "sinistcha"}


def test_damage_range_within_tolerance_is_correct():
    gt = _gt(real_species=_GARCHOMP_SINISTCHA, damage_ranges={("garchomp", "sinistcha", "earthquake"): [(85.3, 101.3)]})
    claim = AtomicClaim(
        claim_type="damage_range", attacker="Garchomp", defender="Sinistcha",
        move="Earthquake", min_percent=84.0, max_percent=100.0,
    )
    assert verify_claim(claim, gt).verdict == "correct"


def test_damage_range_resolves_species_through_the_side_prefix_too():
    gt = _gt(real_species=_GARCHOMP_SINISTCHA, damage_ranges={("garchomp", "sinistcha", "earthquake"): [(85.3, 101.3)]})
    claim = AtomicClaim(
        claim_type="damage_range", attacker="p1 Garchomp", defender="Sinistcha (p2)",
        move="Earthquake", min_percent=84.0, max_percent=100.0,
    )
    assert verify_claim(claim, gt).verdict == "correct"


def test_damage_range_far_off_is_incorrect():
    gt = _gt(
        real_species=_GARCHOMP_SINISTCHA,
        damage_ranges={("garchomp", "sinistcha", "earthquake"): [(85.3, 101.3)]},
        moves_used={"garchomp": {"earthquake"}},
    )
    claim = AtomicClaim(
        claim_type="damage_range", attacker="Garchomp", defender="Sinistcha",
        move="Earthquake", min_percent=10.0, max_percent=15.0,
    )
    v = verify_claim(claim, gt)
    assert v.verdict == "incorrect"


def test_damage_range_for_unused_move_is_incorrect_not_unverifiable():
    gt = _gt(real_species=_GARCHOMP_SINISTCHA, moves_used={"garchomp": {"earthquake"}})
    claim = AtomicClaim(
        claim_type="damage_range", attacker="Garchomp", defender="Sinistcha",
        move="Tackle", min_percent=10.0, max_percent=15.0,
    )
    v = verify_claim(claim, gt)
    assert v.verdict == "incorrect"


def test_damage_range_for_uncomputed_but_real_move_is_unverifiable():
    gt = _gt(real_species=_GARCHOMP_SINISTCHA, moves_used={"garchomp": {"dragonclaw"}}, damage_ranges={})
    claim = AtomicClaim(
        claim_type="damage_range", attacker="Garchomp", defender="Sinistcha",
        move="Dragon Claw", min_percent=40.0, max_percent=50.0,
    )
    assert verify_claim(claim, gt).verdict == "unverifiable"


def test_forme_change_matches_regardless_of_phrasing():
    gt = _gt(real_species={"gengar"}, forme_changes={"gengar": {"gengar"}})  # "-mega" stripped by _norm_forme
    claim = AtomicClaim(claim_type="forme_change", subject="Gengar", forme="Mega Gengar")
    assert verify_claim(claim, gt).verdict == "correct"


def test_forme_change_never_observed_is_incorrect():
    gt = _gt(real_species={"gengar"}, forme_changes={})
    claim = AtomicClaim(claim_type="forme_change", subject="Gengar", forme="Gengar-Mega")
    assert verify_claim(claim, gt).verdict == "incorrect"


def test_stat_stage_direction_match():
    gt = _gt(real_species={"garchomp"}, boost_deltas=[("garchomp", "atk", -1)])
    claim = AtomicClaim(claim_type="stat_stage", subject="Garchomp", stat="atk", stages=-1)
    assert verify_claim(claim, gt).verdict == "correct"


def test_stat_stage_wrong_direction_is_incorrect():
    gt = _gt(real_species={"garchomp"}, boost_deltas=[("garchomp", "atk", -1)])
    claim = AtomicClaim(claim_type="stat_stage", subject="Garchomp", stat="atk", stages=2)
    assert verify_claim(claim, gt).verdict == "incorrect"


def test_protect_block_correct():
    gt = _gt(real_species={"ceruledge"}, blocked_pairs={("ceruledge", "earthquake")})
    claim = AtomicClaim(claim_type="protect_block", subject="Ceruledge", blocked_move="Earthquake")
    assert verify_claim(claim, gt).verdict == "correct"


def test_winner_mismatch_is_incorrect():
    gt = _gt(winner="Ash", winner_aliases={"ash", "p1"}, loser_aliases={"gary", "p2"})
    claim = AtomicClaim(claim_type="winner", subject="Gary")
    assert verify_claim(claim, gt).verdict == "incorrect"


def test_winner_matches_regardless_of_phrasing():
    gt = _gt(winner="b", winner_aliases={"p2", "2", "player2", "b"}, loser_aliases={"p1", "1", "player1", "a"})
    for phrasing in ["player b (p2)", "Player 2", "p2"]:
        claim = AtomicClaim(claim_type="winner", subject=phrasing)
        assert verify_claim(claim, gt).verdict == "correct", phrasing


def test_forfeit_claimed_but_game_was_not_forfeited():
    gt = _gt(forfeited=None)
    claim = AtomicClaim(claim_type="forfeit", subject="Ash")
    v = verify_claim(claim, gt)
    assert v.verdict == "incorrect"
    assert "NOT" in v.reason


def test_forfeit_correct_when_it_matches():
    gt = _gt(forfeited="Ash", forfeited_aliases={"ash", "p1"})
    claim = AtomicClaim(claim_type="forfeit", subject="Ash")
    assert verify_claim(claim, gt).verdict == "correct"


def test_filter_degenerate_claims_drops_trainer_named_pokemon_played():
    """Regression: the judge sometimes collapses "Ash used Torkoal and
    Flutter Mane" into one pokemon_played claim about "Ash" (the trainer)
    instead of one per Pokemon. That must be dropped, not scored either way."""
    gt = _gt(
        real_species={"torkoal", "flutter mane".replace(" ", "")},
        winner_aliases={"ash", "p1"},
        loser_aliases={"gary", "p2"},
    )
    claims = [
        AtomicClaim(claim_type="pokemon_played", subject="Ash"),
        AtomicClaim(claim_type="pokemon_played", subject="Torkoal"),
        AtomicClaim(claim_type="move_used", subject="Gary", move="Fake Out"),
    ]
    kept, dropped = filter_degenerate_claims(claims, gt)
    assert dropped == 2
    assert [c.subject for c in kept] == ["Torkoal"]


def test_filter_degenerate_claims_keeps_everything_when_nothing_is_degenerate():
    gt = _gt(real_species={"torkoal"}, winner_aliases={"ash"}, loser_aliases={"gary"})
    claims = [AtomicClaim(claim_type="pokemon_played", subject="Torkoal")]
    kept, dropped = filter_degenerate_claims(claims, gt)
    assert dropped == 0
    assert len(kept) == 1


def test_condition_result_rates():
    from scripts.faithfulness_benchmark.models import ClaimVerdict, ConditionResult

    claims = [
        ClaimVerdict(claim=AtomicClaim(claim_type="winner", subject="Ash"), verdict="correct"),
        ClaimVerdict(claim=AtomicClaim(claim_type="winner", subject="Gary"), verdict="incorrect"),
        ClaimVerdict(claim=AtomicClaim(claim_type="winner", subject="?"), verdict="unverifiable"),
    ]
    result = ConditionResult(condition="A_grounded", answer="x", claims=claims)
    assert result.strict_rate == 1 / 3
    assert result.lenient_rate == 1 / 2
