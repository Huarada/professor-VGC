"""Explicit BO3 input and results; BO1 widgets and replay state stay separate."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

import streamlit as st

from src.domain.bo3_inference.citations import bound_evidence, describe_hit
from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import Bo3InferenceEvidence, InferenceOptions
from src.domain.exceptions import ProfessorVGCError, SeriesValidationError
from src.domain.interfaces import ProgressReporter
from src.domain.series import Bo3AnalysisRequest, Bo3AnalysisResult, Bo3Evidence
from src.services.container import Container
from src.ui.battle_panel import current_turn_number, render_battle_panel
from src.ui.loading import loading_overlay
from src.ui.results import render_result
from src.ui.series_import import render_series_import
from src.ui.spread_summary import stat_spread_commentary


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
    if provider == "gemini":
        thinking_choice = st.segmented_control(
            "Analysis depth", ["Low", "Medium"], default="Low", required=True,
            key="bo3_thinking_level",
            help="Low is faster. Medium gives Gemini more reasoning time for game reviews and the series summary. "
                 "Damage calculations and stat-point inference are identical in both modes.",
        )
    else:
        thinking_choice = "Low"
    with st.expander("Teamsheet and stat-point inference"):
        enabled = st.checkbox("Infer compatible stat points and natures", value=True, key="bo3_infer_enabled")
        st.caption("Champions IVs are fixed at 31. Hidden stat points are inferred in native Champions units.")
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
    preview_input = (tuple(source.strip() for source in sources), question, teamsheet,
                     perspective, hp_mode, enabled, regulation)
    status_slot = st.empty()
    results_slot = st.empty()  # AI text lands above the deterministic tables once it is ready (ADR-075)
    preview_slot = st.empty()
    with ExitStack() as loading:
        overlay_progress: ProgressReporter | None = None
        status: Any | None = None
        status_detail: Any | None = None
        preview_notice: Any | None = None
        preview_rendered = False

        def report(message: str) -> None:
            if status is not None:
                comparing = "Stage 3 of 3 — AI comparing games:" in message
                status.update(label=message, expanded=True if comparing else None)
                if status_detail is not None:
                    if comparing:
                        status_detail.caption(
                            "Comparing the leads, moves revealed in each game, verified stat-point "
                            "ranges and changes in strategy. Individual game explanations may still "
                            "finish in parallel."
                        )
                    elif "Finishing the remaining game explanations" in message:
                        status_detail.caption(
                            "The series comparison is ready. Waiting for the remaining game reviews."
                        )
                    elif "Revising the series explanation" in message:
                        status_detail.caption(
                            "Rechecking the comparison against verified battle evidence before display."
                        )
                    elif "Checking the final explanation" in message:
                        status_detail.caption(
                            "Checking the explanation against the battle record, Champions rules "
                            "and registered teams."
                        )
                    elif "Preparing" in message and message.startswith("Stage 3 of 3"):
                        status_detail.caption("The AI has finished. Preparing the results for display.")
            elif overlay_progress is not None:
                overlay_progress(message)

        def deterministic_ready(evidence: Bo3Evidence, inferred: Bo3InferenceEvidence | None) -> None:
            nonlocal status, status_detail, preview_notice, preview_rendered
            # The service invokes this on the Streamlit script thread, before
            # starting parallel game reviews. Workers never call Streamlit.
            loading.close()
            preview_rendered = True
            st.session_state["bo3_preview"] = (evidence, inferred)
            st.session_state["bo3_preview_input"] = preview_input
            with preview_slot.container():
                preview_notice = _render_deterministic_preview(evidence, inferred)
            status = status_slot.status("Stage 2 of 3 — Reviewing games with AI...", expanded=False)
            status_detail = status.empty()
            status_detail.caption(
                "Verified score, damage evidence and stat-point ranges are ready below. "
                "The AI is reviewing the games."
            )

        if st.button("Analyze BO3", type="primary"):
            try:
                st.session_state.pop("bo3_preview", None)
                st.session_state.pop("bo3_preview_input", None)
                st.session_state.pop("bo3_result", None)
                supplied = [source.strip() for source in sources]
                count = sum(bool(source) for source in supplied)
                if not count or not all(supplied[:count]):
                    raise SeriesValidationError("Supply games in order without empty gaps.")
                if thinking_choice not in ("Low", "Medium"):
                    raise SeriesValidationError("Choose Low or Medium analysis depth.")
                overlay_progress = loading.enter_context(loading_overlay(
                    title="Analysing your BO3", steps=("Stage 1 of 3 — Reading your BO3 replays...",)))
                resolved = []
                for number, source in enumerate(supplied[:count], 1):
                    report(f"Stage 1 of 3 — Game {number} of {count}: loading the replay log...")
                    resolved.append(container.resolve_replay_text(source))
                request = Bo3AnalysisRequest(session_id=session_id, games=list(resolved),
                                             question=question, provider=provider, teamsheet=teamsheet or None,
                                             inference_options=InferenceOptions(enabled=enabled, perspective=perspective,
                                                                                  hp_mode="exact" if hp_mode == "exact" else "percent"))
                report("Stage 1 of 3 — Checking teamsheets, game order and series score...")
                container.validate_series(request, regulation)
                report("Stage 1 of 3 — Preparing damage validation and per-game analysis...")
                left = container.usage_quota().consume(provider, visitor)
                if left is not None:
                    st.session_state[f"quota_left_{provider}"] = left
                    quota_slot.caption(f"{provider}: {left} analyses left today (resets 00:00 UTC).")
                completed = container.build_bo3_pipeline(
                    provider, orchestrator, regulation, progress=report,
                    thinking_level=thinking_choice.lower(), on_deterministic_ready=deterministic_ready,
                ).analyze(request)
                st.session_state["bo3_result"] = completed
                st.session_state.pop("bo3_preview", None)
                st.session_state.pop("bo3_preview_input", None)
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
                if status is not None:
                    status.update(label="AI explanation failed; verified battle evidence remains available.", state="error")
                    if status_detail is not None:
                        status_detail.caption("The verified score, damage evidence and stat-point ranges remain below.")
                if preview_notice is not None:
                    preview_notice.warning("The AI explanation failed. Verified battle evidence remains available.")
        if st.session_state.get("bo3_error"):
            st.error(st.session_state["bo3_error"])
        result = st.session_state.get("bo3_result")
        if result is None:
            saved_preview = st.session_state.get("bo3_preview")
            if (saved_preview is not None and not preview_rendered and
                    st.session_state.get("bo3_preview_input") == preview_input):
                with preview_slot.container():
                    _render_deterministic_preview(*saved_preview, failed=True)
            return
        if overlay_progress is None and status is None:
            overlay_progress = loading.enter_context(loading_overlay(
                title="Preparing your BO3 results", steps=("Preparing the selected game and analysis tables...",)))
        with results_slot.container():
            _render_series_results(container, result, report)
        if not preview_rendered and result.inference is not None:
            with preview_slot.container():
                render_inference(result.inference)
        if status is not None:
            status.update(label="BO3 analysis complete — game reviews and series explanation are ready.",
                          state="complete", expanded=False)
        if preview_notice is not None:
            preview_notice.success("Battle evidence and AI explanations are ready.")


def _render_deterministic_preview(evidence: Bo3Evidence, inferred: Bo3InferenceEvidence | None,
                                  *, failed: bool = False) -> Any:
    """Publish verified series facts before any game or series model call."""
    if failed:
        notice = st.warning("The AI explanation did not complete. Verified battle evidence from the "
                            "previous attempt remains available.")
    else:
        notice = st.info("Battle evidence is ready. AI game reviews and the series explanation are still running.")
    score = " · ".join(f"{name}: {wins}" for name, wins in evidence.score.items())
    st.markdown(f"**Current series score:** {score}")
    if inferred is not None:
        render_inference(inferred)
    else:
        st.info("Stat-point inference is unavailable for this analysis.")
    return notice


def _render_series_results(container: Container, result: Bo3AnalysisResult, progress: ProgressReporter) -> None:
    """Keep the loading lifecycle open until all result elements are emitted."""
    progress("Stage 3 of 3 - Preparing the replay and game review for display...")
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
            checkpoint = next((entry for entry in result.inference.checkpoints
                               if entry.game == index + 1 and entry.phase == "after_game"), None) if result.inference else None
            render_result(result.games[index], current_turn_number(replay, key_prefix=prefix),
                          stat_spread_commentary=stat_spread_commentary(
                              checkpoint, result.inference.damage_citations if result.inference else ()))
    progress("Stage 3 of 3 - Preparing stat ranges, damage evidence and the series summary...")
    if result.review_timings:
        with st.expander("BO3 timing diagnostics"):
            st.caption("Durations are seconds. The three game reviews overlap; do not add their totals to estimate wall time. "
                       "Model and tool times are callback measurements within each review.")
            st.dataframe([{"Game": timing.game, "Backend": timing.backend,
                "Total": round(timing.total_seconds, 2), "Selection": round(timing.selection_seconds, 2),
                "Evidence": round(timing.evidence_seconds, 2), "Explanation": round(timing.explanation_seconds, 2),
                "Model calls": timing.model_calls, "Model time": round(timing.model_seconds, 2),
                "Tool calls": timing.tool_calls, "Tool time": round(timing.tool_seconds, 2),
                "Tools used": ", ".join(f"{name} x{count}" for name, count in sorted(timing.tool_counts.items()))}
                for timing in result.review_timings], hide_index=True)
            st.caption(f"Inference: {result.inference_seconds or 0:.1f}s · parallel reviews: "
                       f"{result.reviews_wall_seconds or 0:.1f}s · series summary: {result.synthesis_seconds or 0:.1f}s")
    with st.expander("Series review", expanded=True):
        st.markdown(result.answer)
        if result.inference:
            final_checkpoint = next((entry for entry in reversed(result.inference.checkpoints)
                                     if entry.phase == "after_game"), None)
            st.markdown(stat_spread_commentary(final_checkpoint, result.inference.damage_citations))


def render_inference(evidence: Bo3InferenceEvidence) -> None:
    """Display evidence and search coverage without turning sample profiles into facts."""
    st.subheader("Stat-point inference (BO3)")
    st.caption("A 0-32 interval means stat points remain unknown. Marginal ranges are not one complete legal spread "
               "and are not recommended allocations. Suggested spread changes apply to future team preparation.")
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
                       f"Certified damage constraints: {len(checkpoint.local_constraints)}")
            if checkpoint.partial_local_constraints:
                st.caption(f"Partial local searches with certified exclusions: {len(checkpoint.partial_local_constraints)}. "
                           "Only fully proven exclusions narrow ranges; the joint search remains incomplete.")
            if checkpoint.coverage != "exhaustive":
                st.info("The search is incomplete or unsupported. Displayed ranges retain unresolved possibilities.")
            if checkpoint.summaries and all(summary.basis == "declarations" for summary in checkpoint.summaries):
                st.info("No hidden stat-point interval has been narrowed in this checkpoint. Completed local damage "
                        "constraints can reject joint combinations without reducing individual ranges; "
                        "these tables do not identify the original spreads.")
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
            changes = []
            findings = interpret_checkpoint(checkpoint)
            summaries = {summary.pokemon_id: summary for summary in checkpoint.summaries}
            for pokemon in findings.pokemon:
                for stat, change in pokemon.range_reductions.items():
                    if change.removed_integer_values:
                        summary = summaries[pokemon.pokemon_id]
                        role, hits = bound_evidence(checkpoint, summary, stat, evidence.damage_citations, limit=3)
                        changes.append({"Pokemon": pokemon.species + " (" + pokemon.pokemon_id.rsplit(":slot", 1)[0] + ")",
                            "Stat": stat.upper(), "Initial points": f"{change.baseline.minimum}-{change.baseline.maximum}",
                            "Current points": f"{change.current.minimum}-{change.current.maximum}",
                            "Values ruled out": change.removed_integer_values, "Range reduction (%)": change.reduction_percent,
                            "Evidence role": role, "Related damage": "; ".join(describe_hit(hit) for hit in hits)})
            st.markdown("**Range reduction from the teamsheet baseline**")
            if checkpoint.outcome == "conflict":
                st.caption("Conflicting evidence: no new range reduction is credited.")
            elif changes:
                st.dataframe(changes, hide_index=True)
            elif any(pokemon.range_reductions for pokemon in findings.pokemon):
                st.caption("0%: no native stat-point outer interval has narrowed relative to the initial declarations.")
            else:
                st.caption("Baseline comparison is unavailable for this saved result; run a new analysis.")
            st.caption("Reduction counts removed integer values in an outer interval. It is not confidence, accuracy or win probability.")
            attributes = []
            for summary in checkpoint.summaries:
                for envelope in summary.attributes:
                    if not envelope.supported:
                        st.caption(f"{summary.pokemon_id} {envelope.species}: attributes unavailable ({envelope.reason})")
                        continue
                    row = {"Pokemon": summary.pokemon_id, "Form": envelope.species, "Basis": summary.basis}
                    row.update({stat: str(bound.minimum) if bound.minimum == bound.maximum
                                else f"{bound.minimum}-{bound.maximum}" for stat, bound in envelope.attributes.items()})
                    attributes.append(row)
            if attributes:
                st.markdown("**All six attributes - level 50, without battle modifiers**")
                st.caption("Outer ranges derived from compatible stat points and natures. These are not observed "
                           "exact stats or a complete spread. Items, abilities, boosts and field effects can change "
                           "effective battle values. Do not combine all extrema into one set. "
                           "Base and observed Mega forms are listed separately.")
                st.dataframe(attributes, hide_index=True)
            for reason in checkpoint.unsupported:
                st.caption(reason)
            with st.expander("Compatible joint examples (not exact complete sets)"):
                st.json([assignment for assignment in checkpoint.model_dump(mode="json")["joint_examples"]])
    if evidence.damage_citations:
        with st.expander("Eligible logged damage evidence"):
            st.caption("These are observed HP transitions used as inference inputs. A displayed percentage is not "
                       "exact integer damage, and one hit does not prove a complete spread.")
            st.dataframe([{"Game": hit.game, "Event": hit.event_index,
                           "Observed attack": describe_hit(hit)} for hit in evidence.damage_citations],
                         hide_index=True)
    if evidence.evidence_retrievals:
        with st.expander("Evidence consulted across games"):
            st.caption("Queries use only this analysis and the registered player/slot identity. "
                       "Retrieved hits are evidence considered, not necessarily completed constraints or proven spreads.")
            st.dataframe([{
                "Through game": entry.query.through_game,
                "Pokemon": entry.query.pokemon_id or "All registered Pokemon",
                "Attribute": entry.query.stat or "All dependent attributes",
                "Move": entry.query.move or "All eligible moves",
                "Eligible hits": len(entry.observation_ids),
                "Observation IDs": ", ".join(entry.observation_ids),
            } for entry in evidence.evidence_retrievals], hide_index=True)
