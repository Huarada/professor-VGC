"""Tests for cross-turn VGC concept recurrence (concept_tracking.py)."""

from __future__ import annotations

from src.domain.models import ChatMessage
from src.services.concept_tracking import detect_concepts, recurring_concepts


def test_detect_concepts_matches_known_vocabulary_terms():
    assert "Trick Room" in detect_concepts("How does Trick Room change the turn order?")
    assert "Speed control" in detect_concepts("Who moves first with Tailwind up?")
    assert "Protect reads" in detect_concepts("Was that Protect a good read?")


def test_detect_concepts_matches_portuguese_terms():
    assert "Trick Room" in detect_concepts("Como funciona o quarto bizarro nesse turno?")
    assert "Switch prediction" in detect_concepts("Foi uma boa previsão de troca?")


def test_detect_concepts_empty_for_unrelated_text():
    assert detect_concepts("What is Garchomp's best item here?") == []
    assert detect_concepts("") == []
    assert detect_concepts(None) == []  # type: ignore[arg-type]


def test_detect_concepts_can_match_more_than_one_concept():
    concepts = detect_concepts("Should they have used Trick Room instead of Protect?")
    assert "Trick Room" in concepts
    assert "Protect reads" in concepts


def test_recurring_concepts_empty_with_no_history():
    assert recurring_concepts([], "Who moves first here, Trick Room or not?") == []


def test_recurring_concepts_empty_when_current_question_touches_nothing_tracked():
    history = [ChatMessage(role="user", content="Was that Trick Room call correct?")]
    assert recurring_concepts(history, "What's Garchomp's item?") == []


def test_recurring_concepts_empty_when_topic_never_came_up_before():
    history = [ChatMessage(role="user", content="What's Garchomp's item?")]
    assert recurring_concepts(history, "Was that Trick Room call correct?") == []


def test_recurring_concepts_finds_the_earliest_matching_past_question():
    history = [
        ChatMessage(role="user", content="How does Trick Room flip the turn order?"),
        ChatMessage(role="assistant", content="..."),
        ChatMessage(role="user", content="Ok, and does Tailwind stack with it?"),
        ChatMessage(role="assistant", content="..."),
    ]
    result = recurring_concepts(history, "In this game, did Trick Room decide who moved first?")
    assert result == [
        {"concept": "Trick Room", "previous_question": "How does Trick Room flip the turn order?"}
    ]


def test_recurring_concepts_ignores_assistant_messages_as_the_source():
    """Only the USER's own past questions count as "asked about before" —
    an assistant answer mentioning a concept doesn't mean the user asked."""
    history = [
        ChatMessage(role="assistant", content="This is a Trick Room team."),
        ChatMessage(role="user", content="What's the best item for Sinistcha?"),
    ]
    assert recurring_concepts(history, "Was Trick Room active this turn?") == []


def test_recurring_concepts_orders_by_vocabulary_not_history_scan_order():
    """Two concepts recurring at once must always list in the same
    (vocabulary-fixed) order, regardless of which was asked about first."""
    history = [
        ChatMessage(role="user", content="Was that Protect a good read?"),
        ChatMessage(role="user", content="Was Trick Room up that turn?"),
    ]
    result = recurring_concepts(history, "Trick Room and Protect both mattered here, right?")
    assert [r["concept"] for r in result] == ["Trick Room", "Protect reads"]
