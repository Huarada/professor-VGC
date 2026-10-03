"""Streamlit presentation layer — pure view.

Collects input, calls a use case (a pipeline built by the Container), and
renders the returned DTO. Zero business logic, zero calc, zero prompt
engineering here. Rendering lives in sibling modules: theme, landing,
loading, battle_panel, results, audio, icons.

Run with:  streamlit run src/ui/app.py
"""

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
    # st.session_state's own __getitem__ is untyped (Any) by design (it's a
    # dynamic dict-like store) — cast() documents what we know we put in,
    # rather than letting Any silently propagate into every caller.
    return cast(Container, st.session_state["container"])


def _session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())
    return cast(str, st.session_state["session_id"])


def _visitor_id() -> str:
    """Who the per-visitor usage quota (ADR-036) counts against: the client IP.

    Behind Cloud Run the socket peer is Google's front end, so the address
    comes from X-Forwarded-For — its right-most public entry, the one the
    front end appended (entries to its left are client-supplied and could be
    spoofed). Falls back to the socket peer, then to this browser session.
    """
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
    """Sidebar line with the visitor's analyses left today, or None when the
    provider is unlimited. Read once per session and provider, then kept up
    to date by each analysis, so widget reruns cost no Firestore reads."""
    quota = container.usage_quota()
    limit = quota.limit(provider)
    if not limit:
        return None
    key = f"quota_left_{provider}"
    if key not in st.session_state:
        st.session_state[key] = quota.remaining(provider, _visitor_id())
    left = cast(int, st.session_state[key])
    return f"{provider}: {left} of {limit} analyses left today (resets 00:00 UTC)."


# Substrings the underlying provider SDKs (openai, google-generativeai) use in
# their own error messages for a billing/quota shortfall vs. a transient rate
# limit — distinguished here only to point the user at the right fix, never
# to change control flow (the exception is already fatal for this request).
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

    # The marketing hero copy/tag pills and feature-card grid (see
    # hero_section_html/feature_cards_html) only make sense before there's
    # a real analysis to show — matching the Figma design's own
    # `phase === 'idle'`-gated sections. Once a replay has been analyzed,
    # showing them again would just push the real Answer/battle panel
    # further down the page on every rerun for no benefit.
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
        # Regulation controller (ADR-035): pin the analysis to one regulation's
        # data and legal Pokemon, or follow the replay's own regulation.
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
            # Also clears the battle panel's own state (last_replay/
            # last_result/last_error/turn_index) — st.session_state persists
            # across a code-reload rerun (that's its job), so a stale object
            # from before a schema change (e.g. an older ReplayPokemonState
            # missing a newly-added field like `boosts`) can otherwise
            # survive a `git pull` + hot-reload and crash on next render.
            # This button is the in-app recovery for that; a full restart of
            # `streamlit run` plus a fresh browser tab clears it too.
            for key in (
                "session_id", "container", "last_replay", "last_result",
                "last_error", "turn_index",
            ):
                st.session_state.pop(key, None)
            st.rerun()

        # Optional background music — see src/ui/assets/audio/README.md.
        # Renders nothing at all when the folder is empty (the default).
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
            # Big centered spinner, shown for the ENTIRE window below (parse
            # + LLM pipeline + sprite pre-warm), via st.empty() so it can be
            # cleared as one call right before the results render — not
            # st.spinner()'s small inline text, which only ever covered the
            # pipeline.analyze() call and left the (often slower, on a first
            # view of new species — see the pre-warm note below) battle-panel
            # rendering that follows with no "still working" indicator of its
            # own. Reported: the stepper/battle panel visibly became ready
            # before the Answer text did, which read as the app being done
            # when it wasn't — this closes that gap from both ends: nothing
            # in the results area renders until this whole block finishes
            # (unchanged), and now nothing NEW network-bound happens after
            # the overlay clears either.
            loading = st.empty()
            loading.markdown(loading_overlay_html(), unsafe_allow_html=True)
            try:
                # If the pasted text is a recognized Showdown replay URL
                # (play.pokemonshowdown.com/battle-<id> or
                # replay.pokemonshowdown.com/<id>[.json] — the two shapes a
                # user would actually copy/paste), fetch its JSON now, still
                # inside the loading overlay's window, and use THAT as the
                # replay content for everything below instead of the raw
                # pasted URL text (which would just fail to parse as a
                # replay). Anything that isn't a recognized URL — pasted
                # JSON or raw log text — passes through unchanged; this
                # never misidentifies replay content itself as a URL.
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
                    # Parsed independently of, and BEFORE, the LLM pipeline
                    # call below — a second, unrelated parse of the same
                    # resolved text, purely for the visual panel (see
                    # replay_viewer_parser's module docstring for why these
                    # are kept fully decoupled rather than sharing one
                    # parse). A parse failure here must never affect the
                    # LLM call.
                    replay = BattleReplay()
                    if resolved_text:
                        try:
                            replay = container.parse_replay_for_viewer(resolved_text)
                        except Exception:  # noqa: BLE001 - this panel is best-effort only
                            replay = BattleReplay()
                    st.session_state["last_replay"] = replay
                    # Reset the stepper to the first turn (Leads) for a
                    # freshly analyzed battle, rather than leaving it
                    # wherever it was left on a previous, unrelated replay.
                    turn_index = 0
                    st.session_state["turn_index"] = turn_index

                    request = AnalysisRequest(
                        session_id=_session_id(),
                        replay_raw_text=resolved_text or None,
                        question=question.strip(),
                        provider=provider,
                    )
                    try:
                        # Spend one analysis of the visitor's daily quota
                        # first (no-op for an unlimited provider): a refused
                        # analysis must never reach the paid model.
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

                    # Pre-warm the sprite-reachability cache (st.cache_data,
                    # keyed by URL — see ADR-015's follow-up) for exactly the
                    # turn that's about to render, while the overlay is
                    # still up. Without this, a replay with species never
                    # seen before in this server process would do its
                    # first-ever sprite HEAD checks AFTER the overlay
                    # clears, in the render block below — invisible latency
                    # with no spinner covering it, which is the concrete
                    # mechanism behind the reported stagger. The result is
                    # discarded; this call exists only for its caching side
                    # effect, so the real render moments later hits 100%
                    # cache and paints effectively instantly.
                    if replay.snapshots:
                        battle_stage_html(replay, replay.snapshots[turn_index])
            finally:
                loading.empty()

    # Rendered from session_state, OUTSIDE the button's own click branch —
    # Streamlit reruns the whole script on every widget interaction (e.g. the
    # battle panel's own stepper buttons/slider below), and `st.button(...)`
    # only evaluates True on the exact run it was clicked. Tying this render
    # to that branch would make the entire result area (including the panel
    # the stepper itself belongs to) vanish the moment the stepper was used.
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
                # Show the specific failure category plus the detailed message
                # so the user can tell a bad replay from a missing key or a
                # calc issue.
                st.error(f"Analysis failed — {type(error).__name__}: {error}")
                st.caption(_error_tip(error))
                return
            if result is None:
                return
            for warning in container.data_warnings():
                st.warning(warning)

            # Which in-game turn the stepper is currently on — used below to
            # highlight the matching slice of the Answer, and the matching
            # turn-by-turn/protect-read entries, so the slider visually ties
            # the narrative to the battle state it's showing.
            current_turn = current_turn_number(replay)

            render_result(result, current_turn)

    st.markdown(footer_html(), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
