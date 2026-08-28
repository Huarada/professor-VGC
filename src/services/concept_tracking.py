"""Cross-turn VGC concept recurrence — "you asked about X before".

Deliberately the LIGHTEST possible implementation of "adapt to what the user
has already asked about": a fixed, deterministic keyword vocabulary (same
shape/precedent as ``suggestion_service.wants_suggestions``), applied to the
CURRENT question and every past user question already sitting in
``history`` (loaded every turn by every orchestration backend regardless).
No new persistence, no new LLM call, no change to ``ConversationMemory`` —
recurrence is simply re-derived from the conversation history that already
exists, each turn.

Scope, stated honestly: this detects TOPIC recurrence ("this came up
before"), not a graded judgment that the user was wrong or confused last
time — the keyword match has no way to know that, and the explanation
prompt is instructed accordingly (never claim a specific past mistake, only
that the topic recurs). This still delivers the visible "adapts to what you
asked before" behavior a coach product benefits from, without inventing a
correctness claim the deterministic signal can't actually support.
"""

from __future__ import annotations

from typing import Sequence

from src.domain.models import ChatMessage

# Bilingual (EN/PT-BR), same flat-substring convention as
# suggestion_service._INTENT_KEYWORDS. Concept labels are the exact strings
# surfaced back to the user (via the prompt), so they're written as short,
# natural phrases rather than snake_case ids.
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
    """Deterministic keyword match against the fixed vocabulary above —
    same shape as ``suggestion_service.wants_suggestions``. Returns concept
    labels in the vocabulary's own fixed order (never random), so repeated
    calls on the same text always agree."""
    t = (text or "").lower()
    return [concept for concept, keywords in _CONCEPT_KEYWORDS.items() if any(kw in t for kw in keywords)]


def recurring_concepts(
    history: Sequence[ChatMessage], current_question: str
) -> list[dict[str, str]]:
    """For each concept the CURRENT question touches, find the EARLIEST
    past user question (from ``history``, already loaded every turn) that
    touched the same concept. Empty when nothing recurs — the overwhelming
    majority of single-question turns and first-ever turns alike.

    Deliberately re-derived from ``history`` on every call rather than
    persisted separately: ``ConversationMemory.load()`` already returns the
    full session history every turn, so there is nothing new to store, and
    every orchestration backend already has ``history`` in hand before it
    ever calls this.
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
