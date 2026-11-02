"""Unknown original spreads must not be replaced by typical species guesses."""

import pytest

from src.domain.bo3_inference.narration import (
    UNVALIDATED_INVESTMENT_NOTICE,
    contains_speculative_original_spread,
)
from src.services.ground_truth import explain_champions_only


@pytest.mark.parametrize("text", [
    "Gengar often runs Modest nature, maximizing Special Attack.",
    "Vivillon is typically favored with a Timid nature.",
    "Incineroar is commonly seen with Impish nature.",
    "Garchomp likely focused on maximizing speed and attack.",
])
def test_unproven_original_spread_claim_gets_one_correction_then_safe_omission(text):
    assert contains_speculative_original_spread(text)
    corrections = []

    def explain(correction):
        corrections.append(correction)
        return text

    assert explain_champions_only(explain, bo3=True) == UNVALIDATED_INVESTMENT_NOTICE
    assert len(corrections) == 2 and "Declared natures/items/abilities are facts" in corrections[1]
    assert explain_champions_only(lambda correction: text) == text  # BO1 statistical coaching keeps its own contract


@pytest.mark.parametrize("text", [
    "The public teamsheet declares Modest nature. Its original stat spread remains unresolved.",
    "A likely cause is an unknown nature, which remains part of the search.",
    "For future team preparation, consider a Timid nature rather than assuming it was the original spread.",
])
def test_declarations_uncertainty_and_explicit_future_preparation_are_not_original_spread_claims(text):
    assert not contains_speculative_original_spread(text)
    assert explain_champions_only(lambda correction: text, bo3=True) == text
