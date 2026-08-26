"""Unit tests for the deterministic KO-chance / HP-remaining / raw-points /
damage-dealt classifier (scripts/faithfulness_benchmark/percent_classifier.py).

Deliberately split into two groups:
  1. The EXACT phrasings observed in real benchmark runs (run4/run5) that the
     judge previously mis-extracted — proving the fix actually addresses
     what was found.
  2. Phrasings NOT seen anywhere in this benchmark's fixtures — different
     species, different verbs, different sentence shapes — proving the
     rules generalize from the system's own fixed vocabulary rather than
     having memorized this benchmark's specific sentences.
"""

from __future__ import annotations

from scripts.faithfulness_benchmark.percent_classifier import classify_percent_mention

# --- Group 1: exact phrasings observed in real runs (run4.json/run5.json) ---


def test_observed_hp_remaining_phrasings_are_not_damage_dealt():
    cases = [
        ("dealing significant damage and leaving it at 54% HP.", 54.0),
        ("Ceruledge surviving with 40% HP remaining", 40.0),
        ("leaving Staraptor at 64%", 64.0),
        ("ending up at 28% HP after the attack.", 28.0),
        ("which managed to bring Gengar down to 40% HP", 40.0),
    ]
    for text, value in cases:
        assert classify_percent_mention(text, value) == "hp_remaining", text


def test_observed_ko_chance_phrasing_is_not_damage_dealt():
    text = "with a 47.1% chance to 3HKO Garchomp"
    assert classify_percent_mention(text, 47.1) == "ko_chance"


def test_observed_raw_points_phrasing_is_not_a_percent():
    text = "which was projected to deal between 152 and 182 damage to Ceruledge"
    # Judge extracted "152"/"182" as if they were percent — but "182 damage"
    # has no "%" attached anywhere near it.
    assert classify_percent_mention(text, 182.0) == "raw_points"


# --- Group 2: different phrasings, never seen in any fixture — generalization ---


def test_new_ko_chance_phrasing_generalizes():
    # Different species, different move, different sentence shape than
    # anything in fixtures.py.
    for text, value in [
        ("Talonflame's Brave Bird has a 62% chance to OHKO Corviknight.", 62.0),
        ("this hit is a guaranteed 2HKO on Toxapex.", 2.0),  # "2" inside "2HKO"
        ("only a possible 4HKO against Skarmory this turn.", 4.0),
    ]:
        assert classify_percent_mention(text, value) == "ko_chance", text


def test_new_hp_remaining_phrasing_generalizes():
    for text, value in [
        ("Sinistcha ends the turn at 12% HP after the hit.", 12.0),
        ("Milotic remained at 71% HP going into turn 3.", 71.0),
        ("Toxapex is down to 8% HP now.", 8.0),
    ]:
        assert classify_percent_mention(text, value) == "hp_remaining", text


def test_new_raw_points_phrasing_generalizes():
    text = "the attack does 45-52 damage to Ferrothorn this turn"
    assert classify_percent_mention(text, 52.0) == "raw_points"


def test_new_genuine_damage_dealt_phrasing_is_not_rejected():
    for text, value in [
        ("Toxapex's Scald dealt 34.0-40.5% damage to Talonflame.", 34.0),
        ("a 91% hit on Corviknight from Wood Hammer.", 91.0),
        ("this move deals 27.5% to Ferrothorn on average.", 27.5),
    ]:
        assert classify_percent_mention(text, value) == "damage_dealt", text


def test_ko_chance_mentioned_elsewhere_does_not_taint_a_genuine_nearby_percent():
    # The exact failure mode this benchmark had to guard against: one
    # sentence legitimately combining a real damage% with an unrelated
    # KO-chance conclusion far enough away not to be the same fact.
    text = (
        "Garchomp's Earthquake dealt 85.3-101.3% damage to Ceruledge, in an "
        "otherwise unrelated aside worth noting it was also a guaranteed OHKO."
    )
    assert classify_percent_mention(text, 85.3) == "damage_dealt"
    assert classify_percent_mention(text, 101.3) == "damage_dealt"
