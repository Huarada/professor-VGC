"""The full-information corpus scorer must fail wrong inference, not only report right inference."""

import pytest

from scripts.bo3_truth_corpus import (
    discover,
    guarded,
    nature_misses,
    run_series,
    score_checkpoint,
    summarize,
    validate_truth,
)
from src.domain.bo3_inference.models import CandidateProfile, CandidateSummary, DeclaredSet, PointRange
from tests.test_bo3_inference_engine import engine  # noqa: F401

FULL = {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32}


def declared(participant, species, slot):
    return DeclaredSet(participant=participant, slot=slot, species=species)


INITIAL = {"A:slot1": declared("A", "Garchomp", 1), "B:slot1": declared("B", "Raichu", 1)}


def truth(**override):
    teams = {"A": {"Garchomp": {"nature": "Jolly", "points": dict(FULL)}},
             "B": {"Raichu": {"nature": "Timid", "points": dict(FULL)}}}
    teams.update(override)
    return {"teams": teams}


def summary(low, high, *, holes=(), fragile=False, natures=("Jolly",), pokemon_id="A:slot1"):
    stats = {stat: PointRange(minimum=0, maximum=32) for stat in FULL}
    stats["spa"] = PointRange(minimum=low, maximum=high)
    return CandidateSummary(
        pokemon_id=pokemon_id, species="Garchomp", stat_points=stats, natures=natures,
        basis="compatible_assignments", excluded_points={"spa": tuple(holes)} if holes else {},
        single_observation_bounds={"spa": ("g1:e1:p1:Garchomp",)} if fragile else {})


def profile(pokemon_id="A:slot1"):
    return {pokemon_id: CandidateProfile(pokemon_id=pokemon_id, species="Garchomp", points=dict(FULL), natures=("Jolly",))}


def spa_score(**kwargs):
    return next(s for s in score_checkpoint("s", 1, [summary(**kwargs)], profile()) if s.stat == "spa")


def test_the_recorded_series_is_discovered_with_its_truth():
    assert "2709311277" in discover()


def test_a_truth_file_must_cover_both_complete_teams():
    assert validate_truth(truth(), INITIAL) == []
    assert any("no truth" in p for p in validate_truth(truth(B={}), INITIAL))
    assert any("both players" in p for p in validate_truth(truth(), {"A:slot1": INITIAL["A:slot1"]}))


@pytest.mark.parametrize("points,message", [
    ({**FULL, "hp": 33}, "integers 0-32"),
    ({**FULL, "atk": 32}, "above the 66 cap"),
    ({"hp": 2}, "all six stats"),
])
def test_a_malformed_spread_is_rejected(points, message):
    bad = truth(A={"Garchomp": {"nature": "Jolly", "points": points}})
    assert any(message in p for p in validate_truth(bad, INITIAL))


def test_truth_for_an_unregistered_pokemon_is_rejected():
    extra = truth(A={"Garchomp": {"nature": "Jolly", "points": dict(FULL)},
                     "Mew": {"nature": "Timid", "points": dict(FULL)}})
    assert any("never registered" in p for p in validate_truth(extra, INITIAL))


def test_scorer_accepts_an_interval_that_keeps_the_true_point():
    score = spa_score(low=20, high=32)
    assert score.sound and score.removed == 20  # 33 - 13 points


def test_scorer_fails_an_interval_that_excludes_the_true_point():
    assert not spa_score(low=0, high=31).sound


def test_scorer_fails_when_the_true_point_sits_in_an_excluded_hole():
    score = spa_score(low=0, high=32, holes=(32,))
    assert not score.sound and score.removed == 1


def test_scorer_marks_single_observation_dependencies_and_nature_misses():
    assert spa_score(low=20, high=32, fragile=True).fragile
    assert nature_misses([summary(0, 32, natures=("Timid",))], profile()) == ["A:slot1"]
    assert nature_misses([summary(0, 32, natures=("Timid", "Jolly"))], profile()) == []


def test_summary_pools_series_and_reports_each_unsound_stat():
    good = [vars(s) for s in score_checkpoint("s1", 1, [summary(20, 32, fragile=True)], profile())]
    bad = [vars(s) for s in score_checkpoint("s2", 1, [summary(0, 31)], profile())]
    result = summarize([{"scores": good}, {"scores": bad}])
    assert result["soundness"]["count"] == 11 and result["soundness"]["of"] == 12
    assert len(result["unsound"]) == 1 and "s2" in result["unsound"][0] and "spa" in result["unsound"][0]
    assert result["narrowed_stats"]["count"] == 2 and result["fragile_among_narrowed"]["count"] == 1


def test_summary_uses_only_the_last_checkpoint_of_each_series():
    early = [vars(s) for s in score_checkpoint("s", 1, [summary(0, 31)], profile())]
    late = [vars(s) | {"game": 2} for s in score_checkpoint("s", 1, [summary(20, 32)], profile())]
    assert summarize([{"scores": early + late}])["unsound"] == []


def test_one_failing_series_is_reported_and_does_not_hide_the_rest(monkeypatch):
    import scripts.bo3_truth_corpus as corpus
    from src.domain.exceptions import SeriesValidationError

    def boom(*args):
        raise SeriesValidationError("contradicts a replay reveal")

    monkeypatch.setattr(corpus, "run_series", boom)
    failed = guarded(None, "s9", None, None, "propagation")
    assert failed == {"series": "s9", "error": "SeriesValidationError: contradicts a replay reveal"}
    good = [vars(s) for s in score_checkpoint("s1", 1, [summary(20, 32)], profile())]
    result = summarize([failed, {"scores": good}])
    assert result["errors"] == ["s9: SeriesValidationError: contradicts a replay reveal"]
    assert result["soundness"]["of"] == 6 and result["unsound"] == []


def test_recorded_series_is_sound_and_propagation_narrows_where_legacy_does_not(engine):  # noqa: F811
    public, truth_path = discover()["2709311277"]
    fresh = run_series(engine, "2709311277", public, truth_path, "propagation")
    result = summarize([fresh])
    assert result["unsound"] == [] and result["nature_misses"] == [] and result["invalid_truth"] == []
    assert result["pokemon_stats"] == 72 and result["narrowed_stats"]["count"] >= 15
