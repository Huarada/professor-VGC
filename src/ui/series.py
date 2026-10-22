"""Explicit BO3 input and results; BO1 widgets and replay state stay separate."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.domain.bo3_inference.models import Bo3InferenceEvidence, InferenceOptions
from src.domain.exceptions import ProfessorVGCError, SeriesValidationError
from src.domain.series import Bo3AnalysisRequest
from src.services.container import Container
from src.ui.battle_panel import current_turn_number, render_battle_panel
from src.ui.results import render_result
from src.ui.series_import import render_series_import


def render_series(
    container: Container, *, session_id: str, provider: str, orchestrator: str,
    regulation: str, visitor: str, quota_slot: Any,
) -> None:
    """Collect ordered replays, validate all of them, then spend one analysis slot."""
    st.subheader("1 · Best of three")
    st.caption("Pokemon Champions · Mega Evolution only. Import a series or paste individual games in order.")
    render_series_import(container)
    with st.expander("Review or paste individual games", expanded=False):
        sources = [st.text_area(f"Game {i} replay JSON, log or URL", key=f"bo3_source_{i}", height=120)
                   for i in range(1, 4)]
    question = st.text_input("Ask ProfessorVGC about the series", key="bo3_question")
    with st.expander("Teamsheet and stat-point inference"):
        enabled = st.checkbox("Infer compatible stat points and natures", value=True, key="bo3_infer_enabled")
        st.caption("Champions IVs are fixed at 31. Unknown investment is searched in native stat points, not legacy EV units.")
        teamsheet = st.text_area(
            "Teamsheet JSON (optional)", key="bo3_teamsheet", height=160,
            placeholder='{"registered_team": true, "pokemon": [{"participant": "Alice", "slot": 1, '
                        '"species": "Garchomp", "item": "Life Orb", "ability": "Rough Skin", '
                        '"nature": "Adamant", "stat_points": {"atk": 32}}]}',
        )
        perspective = st.text_input("Knowledge perspective", value="public", key="bo3_infer_perspective",
                                    help="public, a player's name for their private declarations, or analyst for retrospective access.")
        hp_mode = st.selectbox("Replay HP visibility", ["percent", "exact"], key="bo3_infer_hp",
                               help="Use percent for public replays. Exact requires a private log exposing real HP/max HP.")
        st.caption("Missing fields remain unknown. Set registered_team to true only when the same registered sets apply to all games.")
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
                                             question=question, provider=provider, teamsheet=teamsheet or None,
                                             inference_options=InferenceOptions(enabled=enabled, perspective=perspective,
                                                                                  hp_mode="exact" if hp_mode == "exact" else "percent"))
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
                st.session_state.pop("bo3_turn_positions", None)
                st.session_state.pop("bo3_review_game", None)
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
    index = st.selectbox("Review game", range(len(result.games)),
                         key="bo3_review_game", format_func=lambda i: f"Game {i + 1}")
    replay = container.parse_replay_for_viewer(st.session_state["bo3_replays"][index])
    prefix = f"bo3_game_{index}_"
    positions = st.session_state.setdefault("bo3_turn_positions", {})
    if f"{prefix}turn_index" not in st.session_state:
        st.session_state[f"{prefix}turn_index"] = positions.get(index, 0)
    field, review = st.columns([1, 2])
    with field:
        if replay.snapshots:
            render_battle_panel(replay, key_prefix=prefix)
            positions[index] = st.session_state[f"{prefix}turn_index"]
        else:
            st.caption("No battlefield snapshots are available for this game.")
    with review:
        with st.expander(f"Game {index + 1} evidence and review", expanded=True):
            render_result(result.games[index], current_turn_number(replay, key_prefix=prefix))
    if result.inference is not None:
        render_inference(result.inference)
    with st.expander("Series review", expanded=True):
        st.markdown(result.answer)


def render_inference(evidence: Bo3InferenceEvidence) -> None:
    """Display evidence and search coverage without turning sample profiles into facts."""
    st.subheader("Stat-point inference (BO3)")
    st.caption(f"IVs fixed at 31 · perspective: {evidence.perspective} · continuity: {evidence.continuity}")
    for limitation in evidence.limitations:
        st.caption(limitation)
    for checkpoint in evidence.checkpoints:
        if checkpoint.phase != "after_game":
            continue
        with st.expander(f"After game {checkpoint.game}: {checkpoint.outcome} ({checkpoint.coverage})",
                         expanded=checkpoint == evidence.checkpoints[-1]):
            st.caption(f"Eligible damage observations: {len(checkpoint.observation_ids)} · "
                       f"Joint assignments evaluated: {checkpoint.evaluated_assignments} · "
                       f"Exhaustive local constraints: {len(checkpoint.local_constraints)}")
            if checkpoint.coverage != "exhaustive":
                st.info("The search is incomplete or unsupported. Displayed ranges retain unresolved possibilities.")
            if checkpoint.outcome == "conflict":
                st.warning("Declarations and supported observations conflict; the last valid ranges are retained.")
            rows = []
            for summary in checkpoint.summaries:
                row = {"Pokemon": summary.pokemon_id + " " + summary.species, "Basis": summary.basis,
                       "Natures": ", ".join(summary.natures),
                       "Declared stats": ", ".join(summary.declared_stat_points), "Nature declared": summary.nature_declared}
                row.update({stat: f"{bound.minimum}–{bound.maximum}" for stat, bound in summary.stat_points.items()})
                rows.append(row)
            st.dataframe(rows, hide_index=True)
            for reason in checkpoint.unsupported:
                st.caption(reason)
            with st.expander("Compatible joint examples (not exact complete sets)"):
                st.json([assignment for assignment in checkpoint.model_dump(mode="json")["joint_examples"]])
