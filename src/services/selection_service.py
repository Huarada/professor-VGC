"""Selection stage — the 1st AI, native (LLMProvider) implementation.

Delegates parsing/sanitizing to :mod:`src.services.selection_logic` (which now
enforces cross-side matchups) so the LangChain orchestrator produces identical
selection plans.
"""

from __future__ import annotations

from typing import Sequence

from src.adapters.llm.prompts import load_prompt
from src.domain.interfaces import LLMProvider
from src.domain.models import AnalysisRequest, ChatMessage, GameState, SelectionPlan
from src.services.battle_context import candidate_species, outcome_summary, rosters
from src.services.selection_logic import (
    build_selection_input,
    parse_selection,
    sanitize_plan,
)


class LLMSelectionService:
    """Concrete :class:`~src.domain.interfaces.SelectionStrategy`."""

    def __init__(
        self, llm: LLMProvider, *, temperature: float = 0.0, max_matchups: int = 6
    ) -> None:
        self._llm = llm
        self._temperature = temperature
        self._max_matchups = max_matchups
        self._system = load_prompt("selection_system")

    def select(
        self,
        *,
        request: AnalysisRequest,
        game_state: GameState,
        history: Sequence[ChatMessage],
    ) -> SelectionPlan:
        species = candidate_species(game_state)
        side_of = game_state.side_of()
        user_turn = ChatMessage(
            role="user",
            content=build_selection_input(
                game_state.format_id,
                rosters(game_state),
                request.question,
                outcome_summary(game_state),
            ),
        )
        raw = self._llm.complete(
            system=self._system,
            messages=[*history, user_turn],
            temperature=self._temperature,
            json_mode=True,
        )
        plan = parse_selection(raw, species, side_of)
        return sanitize_plan(plan, species, self._max_matchups, side_of)
