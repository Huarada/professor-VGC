"""Explicit BO3 input and results; BO1 widgets and replay state stay separate."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.domain.exceptions import ProfessorVGCError, SeriesValidationError
from src.domain.series import Bo3AnalysisRequest
from src.services.container import Container
from src.ui.battle_panel import current_turn_number, render_battle_panel
from src.ui.results import render_result


def render_series(
    container: Container, *, session_id: str, provider: str, orchestrator: str,
    regulation: str, visitor: str, quota_slot: Any,
) -> None:
    """Collect ordered replays, validate all of them, then spend one analysis slot."""
    st.subheader("1 · Best of three")
    st.caption("Pokemon Champions · Mega Evolution only. Paste games in order; leave unplayed games empty.")
    sources = [st.text_area(f"Game {i} replay JSON, log or URL", key=f"bo3_source_{i}", height=120)
               for i in range(1, 4)]
    question = st.text_input("Ask ProfessorVGC about the series", key="bo3_question")
    st.caption("Reviews each supplied game and produces a series summary. One submission uses one daily analysis slot.")
    if st.button("Analyze BO3", type="primary"):
        try:
            supplied = [source.strip() for source in sources]
            count = sum(bool(source) for source in supplied)
            if not count or not all(supplied[:count]):
                raise SeriesValidationError("Supply games in order without empty gaps.")
            with st.spinner("Reviewing the series..."):
                resolved = [container.resolve_replay_text(source) for source in supplied[:count]]
                request = Bo3AnalysisRequest(session_id=session_id, games=list(resolved),
                                             question=question, provider=provider)
                container.validate_series(request, regulation)
                left = container.usage_quota().consume(provider, visitor)
                if left is not None:
                    st.session_state[f"quota_left_{provider}"] = left
                    quota_slot.caption(f"{provider}: {left} analyses left today (resets 00:00 UTC).")
                completed = container.build_bo3_pipeline(provider, orchestrator, regulation).analyze(request)
                st.session_state["bo3_result"] = completed
                st.session_state["bo3_replays"] = resolved
                for key in list(st.session_state):
                    if str(key).startswith("bo3_game_"):
                        st.session_state.pop(key, None)
                st.session_state.pop("bo3_error", None)
        except ProfessorVGCError as exc:
            st.session_state.pop("bo3_result", None)
            st.session_state["bo3_error"] = str(exc)
    if st.session_state.get("bo3_error"):
        st.error(st.session_state["bo3_error"])
    result = st.session_state.get("bo3_result")
    if result is None:
        return
    score = " · ".join(f"{name}: {wins}" for name, wins in result.evidence.score.items())
    st.subheader(f"Series score — {score}")
    st.caption("Complete" if result.evidence.complete else "Incomplete series")
    for limitation in result.evidence.limitations:
        st.info(limitation)
    st.markdown(result.answer)
    index = st.selectbox("Review game", range(len(result.games)),
                         key="bo3_review_game", format_func=lambda i: f"Game {i + 1}")
    with st.expander(f"Game {index + 1} evidence and review", expanded=True):
        replay = container.parse_replay_for_viewer(st.session_state["bo3_replays"][index])
        prefix = f"bo3_game_{index}_"
        render_result(result.games[index], current_turn_number(replay, key_prefix=prefix))
        if replay.snapshots:
            render_battle_panel(replay, key_prefix=prefix)
