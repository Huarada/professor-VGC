"""Streamlit UI — pure view: collects input, calls a Container use case and
renders the DTO. Run with ``streamlit run src/ui/app.py``."""

from __future__ import annotations

import ipaddress
import uuid
from typing import cast

import streamlit as st

from src.domain.exceptions import (
    LLMProviderError,
    ProfessorVGCError,
    RegulationMismatchError,
    ReplayFetchError,
    UsageLimitExceededError,
    UsageQuotaError,
)
from src.domain.models import AnalysisRequest
from src.domain.replay_view_models import BattleReplay
from src.services.container import Container
from src.ui.audio import render_background_music
from src.ui.battle_panel import battle_stage_html, current_turn_number, render_battle_panel
from src.ui.icons import POKEBALL_ICON, icon_md
from src.ui.landing import (
    ambient_background_html,
    feature_cards_html,
    footer_html,
    grass_row_html,
    hero_header_html,
    hero_section_html,
)
from src.ui.loading import loading_overlay_html
from src.ui.results import render_result
from src.ui.theme import inject_global_styles


def _get_container() -> Container:
    if "container" not in st.session_state:
        st.session_state["container"] = Container()
    # st.session_state is untyped; cast() records what was stored.
    return cast(Container, st.session_state["container"])


def _session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())
    return cast(str, st.session_state["session_id"])


def _visitor_id() -> str:
    """The client IP the usage quota counts against (ADR-036): the right-most
    public X-Forwarded-For entry (left ones are spoofable), else the socket
    peer, else this browser session."""
    forwarded = st.context.headers.get("X-Forwarded-For") or ""
    for hop in reversed([part.strip() for part in forwarded.split(",")]):
        try:
            if ipaddress.ip_address(hop).is_global:
                return hop
        except ValueError:
            continue
    peer = getattr(st.context, "ip_address", None)
    return peer if isinstance(peer, str) and peer else _session_id()


def _quota_caption(container: Container, provider: str) -> str | None:
    """Analyses left today (None = unlimited); read once per session, then
    updated by each analysis, so reruns cost no Firestore reads."""
    quota = container.usage_quota()
    limit = quota.limit(provider)
    if not limit:
        return None
    key = f"quota_left_{provider}"
    if key not in st.session_state:
        st.session_state[key] = quota.remaining(provider, _visitor_id())
    left = cast(int, st.session_state[key])
    return f"{provider}: {left} of {limit} analyses left today (resets 00:00 UTC)."


# Provider SDK error substrings, only to pick the right tip.
_QUOTA_HINTS = ("insufficient_quota", "credit_balance_exhausted", "no credits", "billing")
_RATE_LIMIT_HINTS = ("rate limit", "429", "resource_exhausted", "quota")


def _error_tip(exc: Exception) -> str:
    """A caption pointing at the likely fix, tailored to the failure category."""
    if isinstance(exc, LLMProviderError):
        text = str(exc).lower()
        if any(hint in text for hint in _QUOTA_HINTS):
            return (
                "Tip: your LLM provider account is out of credits/quota. Add "
                "billing at your provider's dashboard, or switch provider in "
                "the sidebar (openai ↔ gemini) if the other one still has "
                "credit — deterministic calcs are unaffected, only the final "
                "explanation needs the LLM."
            )
        if any(hint in text for hint in _RATE_LIMIT_HINTS):
            return (
                "Tip: the provider is rate-limiting requests. Wait a moment "
                "and try again, or switch provider in the sidebar."
            )
        return (
            "Tip: the LLM provider call failed. Check `PROFESSORVGC_OPENAI_API_KEY` / "
            "`PROFESSORVGC_GEMINI_API_KEY` are set and valid, and that the selected "
            "provider in the sidebar matches a key you actually have."
        )
    if isinstance(exc, UsageLimitExceededError):
        return (
            "Tip: this provider has a daily per-visitor limit on this deployment. "
            "Switch provider in the sidebar, or come back after 00:00 UTC."
        )
    if isinstance(exc, UsageQuotaError):
        return (
            "Tip: the usage quota could not be checked, so the analysis was not "
            "run. Try again in a moment, or switch provider in the sidebar."
        )
    if isinstance(exc, RegulationMismatchError):
        return (
            "Tip: the regulation controller in the sidebar is pinned to a different "
            "regulation than this replay. Pick the replay's regulation (or 'Auto')."
        )
    if isinstance(exc, ReplayFetchError):
        return (
            "Tip: the replay URL couldn't be fetched — double check it's a real, "
            "still-existing replay (a deleted or private one returns this same "
            "error), or paste the replay JSON/log text directly instead."
        )
    return (
        "Tip: paste the full Showdown replay JSON (including its \"log\" "
        "field), the raw battle log, or a Showdown replay URL. For provider "
        "errors, check your `PROFESSORVGC_OPENAI_API_KEY` / "
        "`PROFESSORVGC_GEMINI_API_KEY`."
    )


def main() -> None:
    st.set_page_config(page_title="ProfessorVGC", page_icon=POKEBALL_ICON, layout="wide")
    inject_global_styles()
    st.markdown(ambient_background_html(), unsafe_allow_html=True)
    st.markdown(hero_header_html(), unsafe_allow_html=True)
    st.caption("Deterministic damage-calc + Chaos metagame stats + LLM explainability.")

    # Landing sections only before the first analysis.
    is_idle = "last_replay" not in st.session_state and "last_result" not in st.session_state
    if is_idle:
        st.markdown(hero_section_html(), unsafe_allow_html=True)

    with st.sidebar:
        st.header("Configuration (BYOK)")
        provider = st.selectbox("LLM provider", ["gemini", "openai"], index=0)
        orchestrator = st.selectbox(
            "Orchestration", ["adk", "langchain", "native"], index=0,
            help="Google ADK agents (default), LangChain LCEL chains, or the "
                 "hand-rolled native pipeline.",
        )
        # Regulation controller (ADR-035): pin a regulation or follow the replay's.
        choices = _get_container().regulation_choices()
        configured = _get_container().settings.regulation
        regulation = st.selectbox(
            "Regulation",
            list(choices),
            index=list(choices).index(configured) if configured in choices else 0,
            format_func=lambda key: choices[key],
            help="Pinned: usage data, Smogon sets and the Pokemon the answer may "
                 "mention come from that regulation only, and a replay from another "
                 "regulation is refused. Auto: the replay's own regulation.",
        )
        # A placeholder, refilled right after an analysis spends one: the
        # sidebar renders before the Analyze click is handled below.
        quota_slot = st.empty()
        try:
            caption = _quota_caption(_get_container(), provider)
        except ProfessorVGCError as exc:
            caption = f"{provider}: usage quota unavailable ({type(exc).__name__})."
        if caption:
            quota_slot.caption(caption)
        st.info(
            f"{icon_md(POKEBALL_ICON)} Set your key via environment variables:\n"
            "`PROFESSORVGC_OPENAI_API_KEY` or `PROFESSORVGC_GEMINI_API_KEY`."
        )
        if st.button("Reset conversation"):
            # Also drops panel state that could be stale after a schema change + hot reload.
            for key in (
                "session_id", "container", "last_replay", "last_result",
                "last_error", "turn_index",
            ):
                st.session_state.pop(key, None)
            st.rerun()

        # Optional background music (renders nothing when the folder is empty).
        render_background_music()

    container = _get_container()

    st.subheader("1 · Replay")
    replay_text = st.text_area(
        "Paste the Showdown replay JSON, raw battle log, or a replay URL",
        height=200,
        placeholder=(
            '{"format": "gen9vgc2025", "sides": [...]}  — or raw |...| log  — or '
            "https://play.pokemonshowdown.com/battle-gen9vgc2025regh-1234567890"
        ),
    )

    st.subheader("2 · Question")
    question = st.text_input(
        "Ask ProfessorVGC",
        placeholder="e.g. Does my Garchomp OHKO their Sinistcha? What's the safe swap?",
    )

    analyze_clicked = st.button("Analyze", type="primary")
    if is_idle:
        st.markdown(grass_row_html(), unsafe_allow_html=True)
        st.markdown(feature_cards_html(), unsafe_allow_html=True)

    if analyze_clicked:
        if not replay_text.strip() and not question.strip():
            st.warning("Provide a replay and/or a question.")
        else:
            # Full-screen overlay for the whole parse + pipeline + sprite pre-warm, so
            # nothing in the results renders before the answer is ready.
            loading = st.empty()
            loading.markdown(loading_overlay_html(), unsafe_allow_html=True)
            try:
                # A pasted Showdown replay URL is replaced by its fetched JSON.
                resolved_text = replay_text.strip()
                fetch_error: ReplayFetchError | None = None
                try:
                    resolved_text = container.resolve_replay_text(resolved_text)
                except ReplayFetchError as exc:
                    fetch_error = exc

                if fetch_error is not None:
                    st.session_state["last_replay"] = BattleReplay()
                    st.session_state["last_result"] = None
                    st.session_state["last_error"] = fetch_error
                else:
                    # Best-effort panel view; a failure here never affects the analysis.
                    replay = BattleReplay()
                    if resolved_text:
                        try:
                            replay = container.parse_replay_for_viewer(resolved_text)
                        except Exception:  # noqa: BLE001 - this panel is best-effort only
                            replay = BattleReplay()
                    st.session_state["last_replay"] = replay
                    turn_index = 0
                    st.session_state["turn_index"] = turn_index

                    request = AnalysisRequest(
                        session_id=_session_id(),
                        replay_raw_text=resolved_text or None,
                        question=question.strip(),
                        provider=provider,
                    )
                    try:
                        # Spend quota first: a refused analysis must never reach the paid model.
                        quota_left = container.usage_quota().consume(provider, _visitor_id())
                        if quota_left is not None:
                            st.session_state[f"quota_left_{provider}"] = quota_left
                            quota_slot.caption(_quota_caption(container, provider) or "")
                        pipeline = container.build_pipeline(provider, orchestrator, regulation)
                        st.session_state["last_result"] = pipeline.analyze(request)
                        st.session_state["last_error"] = None
                    except ProfessorVGCError as exc:
                        st.session_state["last_result"] = None
                        st.session_state["last_error"] = exc

                    # Pre-warm the sprite cache for the first rendered turn while the overlay is up.
                    if replay.snapshots:
                        battle_stage_html(replay, replay.snapshots[turn_index])
            finally:
                loading.empty()

    # Rendered outside the click branch: widgets rerun the script and st.button is
    # True only on the click's own run.
    if "last_replay" in st.session_state or "last_result" in st.session_state:
        left, main_col = st.columns([1, 2])
        with left:
            replay = st.session_state.get("last_replay") or BattleReplay()
            if replay.snapshots:
                render_battle_panel(replay)
            else:
                st.caption("No battle replay to visualize for this input.")

        with main_col:
            error = st.session_state.get("last_error")
            result = st.session_state.get("last_result")
            if error is not None:
                st.error(f"Analysis failed — {type(error).__name__}: {error}")
                st.caption(_error_tip(error))
                return
            if result is None:
                return
            for warning in container.data_warnings():
                st.warning(warning)

            # Turn shown by the stepper; highlights the matching parts of the answer.
            current_turn = current_turn_number(replay)

            render_result(result, current_turn)

    st.markdown(footer_html(), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
