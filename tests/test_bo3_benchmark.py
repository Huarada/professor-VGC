"""Benchmark judging must catch wrong results and must not leak evaluation truth."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.bo3_inference_benchmark.fixtures import Corpus
from scripts.bo3_inference_benchmark.independent import validate_oracle
from scripts.bo3_inference_benchmark.runner import run_fixture
from tests.test_bo3_inference import AnalyticProjection, log, sheet

CORPUS = Path(__file__).resolve().parents[1] / "docs/bo3-inference/validation/fixtures.json"


def analytic_fixture():
    from scripts.bo3_inference_benchmark.fixtures import Fixture

    submitted = sheet()
    truth = sheet(known_defense=True)
    return Fixture(name="independent_fixture", games=[log()], teamsheet=submitted,
                   truth={f"{entry['participant']}:slot1": entry for entry in truth["pokemon"]},
                   options={"hp_mode": "exact"})


def test_independent_oracle_checks_joint_counts_ranges_and_false_uniqueness():
    rows = validate_oracle()
    assert len(rows) == 17
    assert all(row["passed"] for row in rows)
    assert rows[0]["expected_pairs"] == rows[-1]["expected_pairs"] == 1
    assert rows[8]["expected_pairs"] == 33


def test_judge_does_not_treat_incompatible_truth_as_retained():
    fixture = analytic_fixture()
    assert run_fixture(fixture, AnalyticProjection())["passed"]
    target = fixture.truth["Bob:slot1"].model_copy(update={"stat_points": {"hp": 10, "def": 19}})
    corrupted = fixture.model_copy(update={"truth": {**fixture.truth, "Bob:slot1": target}})
    row = run_fixture(corrupted, AnalyticProjection())
    assert not row["passed"]
    assert not row["checks"]["truth_satisfies_all_modeled_damage"]
    assert not row["checks"]["truth_in_reported_outer_ranges"]


def test_private_evaluation_truth_is_never_submitted_to_solver():
    fixture = analytic_fixture()
    port = AnalyticProjection()
    row = run_fixture(fixture, port)
    assert row["passed"]
    # Truth is used only after search: the search must still try defenses other than 20.
    assert len({request.defender_points.get("def") for request in port.requests}) > 1
    assert "def" not in fixture.teamsheet.pokemon[1].stat_points


def test_committed_corpus_contains_all_matrix_axes_and_budget_negatives():
    corpus = Corpus.model_validate_json(CORPUS.read_text(encoding="utf-8"))
    assert len(corpus.fixtures) == 26
    assert sum(f.equivalent_to is not None for f in corpus.fixtures) == 12
    assert {f.options.hp_mode for f in corpus.fixtures} == {"exact", "percent"}
    assert sum(f.expected_coverage == "bounded" for f in corpus.fixtures) == 2
    assert all(len(f.games) == 3 for f in corpus.fixtures)
    assert all(f.provenance == "engine_generated" for f in corpus.fixtures)


def test_empty_duplicate_and_forward_reference_corpora_are_rejected():
    corpus = Corpus.model_validate_json(CORPUS.read_text(encoding="utf-8"))
    first = corpus.fixtures[0]
    for fixtures in ([], [first, first], [first.model_copy(update={"equivalent_to": "future"})]):
        with pytest.raises(ValidationError):
            Corpus(generator_engine_version="test", rule_version="test", fixtures=fixtures)
