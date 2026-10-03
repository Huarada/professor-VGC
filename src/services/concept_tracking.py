"""Cross-turn topic recurrence ("you asked about X before").

A fixed keyword vocabulary over the current question and past questions in
``history`` — no new storage or LLM call. It detects that a topic recurs,
not that the user was wrong, and the prompt is told so.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.models import ChatMessage

# EN/PT-BR substrings; labels are shown to the user, so they are phrases.
_CONCEPT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Trick Room": (
        "trick room", "trickroom", "quarto bizarro",
    ),
    "Speed control": (
        "speed control", "controle de velocidade", "tailwind", "scarf",
        "paraliz", "paralysis", "quem é mais rápido", "quem ataca primeiro",
        "who moves first", "who is faster", "who's faster", "speed tier",
    ),
    "Switch prediction": (
        "switch", "troca", "trocar", "previs", "predict", "leitura",
        " read ", "reads",
    ),
    "Protect reads": (
        "protect", "detect", "spiky shield", "wide guard", "proteg",
    ),
    "KO chance / damage calc": (
        "ohko", "2hko", "3hko", "4hko", "chance de ko", "ko chance",
        "chance de", "cálculo de dano", "calculo de dano", "damage calc",
    ),
    "Type coverage": (
        "coverage", "cobertura", "type match", "efetividade",
        "super effective", "super efetivo",
    ),
    "EV spread / nature": (
        "evs", "ev spread", "investimento de ev", "spread de ev",
        "nature", "natureza",
    ),
    "Team synergy": (
        "sinergia", "synergy", "parceiro", "teammate", "core do time",
        "estrutura de time",
    ),
}


def detect_concepts(text: str) -> list[str]:
    """Concept labels in ``text``, in the vocabulary's fixed order."""
    t = (text or "").lower()
    return [concept for concept, keywords in _CONCEPT_KEYWORDS.items() if any(kw in t for kw in keywords)]


def recurring_concepts(
    history: Sequence[ChatMessage], current_question: str
) -> list[dict[str, str]]:
    """For each concept in the current question, the earliest past question that
    touched it (usually none). Re-derived from ``history`` every call.
    """
    current = detect_concepts(current_question)
    if not current:
        return []
    current_set = set(current)
    seen: dict[str, str] = {}
    for message in history:
        if message.role != "user":
            continue
        for concept in detect_concepts(message.content):
            if concept in current_set and concept not in seen:
                seen[concept] = message.content
    # Preserve the vocabulary's fixed order (not dict-insertion order from
    # the history scan) so the same pair of concepts always lists the same
    # way regardless of which one was asked about first historically.
    return [
        {"concept": concept, "previous_question": seen[concept]}
        for concept in _CONCEPT_KEYWORDS
        if concept in seen
    ]
