"""Rendering of one AnalysisResult (answer, evidence panels, debug expanders)."""

from __future__ import annotations

import streamlit as st

from src.domain.models import AnalysisResult, ProtectRead, TurnCheck
from src.ui.battle_panel import highlight_answer_by_turn
from src.ui.icons import PIKACHU_ICON, POKEBALL_ICON, icon_html, icon_md


def render_result(result: AnalysisResult, current_turn: int) -> None:
    """Render one AnalysisResult; ``current_turn`` is highlighted throughout."""
    st.subheader("Answer")
    if result.regulation is not None:
        mode = "pinned" if result.regulation.strict else "from the replay"
        st.caption(
            f"Regulation: **{result.regulation.label}** ({mode}) — data and legal "
            f"Pokemon from `{result.regulation.format_id}` only"
        )
    for warning in result.regulation_warnings:
        st.warning(f"{icon_md(PIKACHU_ICON)} {warning}")
    st.markdown(highlight_answer_by_turn(result.answer, current_turn))
    _render_agent_calls(result)

    if result.battle_result:
        with st.expander("Battle result (from the replay log)", expanded=True):
            st.text(result.battle_result)

    if not any(s.top_moves for s in result.meta_context.pokemon_stats.values()):
        st.info(
            f"{icon_md(POKEBALL_ICON)} No metagame (Chaos) data was loaded for "
            "these Pokemon, so the Smogon strategy section is empty. Load a Chaos "
            "dump covering this format into Firestore — "
            "`python -m scripts.migrate_chaos_to_firestore` or "
            "`scripts.sync_smogon_chaos_to_firestore` — see DATA.md."
        )

    _render_verdicts(result)
    if result.turn_checks:
        _render_turn_checks(result.turn_checks, current_turn)
    if result.protect_reads:
        _render_protect_reads(result.protect_reads, current_turn)

    with st.expander("Selection plan (1st AI)"):
        st.json(result.selection.model_dump())
    with st.expander("Metagame context (Chaos)"):
        st.json(result.meta_context.model_dump())
    with st.expander("Strategies (Smogon-derived)"):
        st.json([s.model_dump() for s in result.strategies])


def _render_agent_calls(result: AnalysisResult) -> None:
    # ADR-028: the agent backends' (ADK, LangChain) explanation agent can
    # reach back into damage_calc/chaos_meta_stats/smogon_strategy mid-answer,
    # for a question the precomputed context didn't already cover (a
    # hypothetical item, a different tier, ...). Flag it plainly whenever it
    # happened, since those figures are fresh/hypothetical lookups, not part
    # of the precomputed ground truth for the real game.
    if not result.agent_tool_calls:
        return
    failed = [c for c in result.agent_tool_calls if not c.ok]
    tool_names = ", ".join(sorted({c.tool for c in result.agent_tool_calls}))
    count = len(result.agent_tool_calls)
    banner = st.warning if failed else st.info
    icon = PIKACHU_ICON if failed else POKEBALL_ICON
    banner(
        f"{icon_md(icon)} The AI reached back into live data mid-answer — "
        f"{count} on-demand lookup{'s' if count != 1 else ''} ({tool_names}) "
        "beyond the precomputed ground truth above. This only happens on the "
        "agent backends (ADK, LangChain), for questions that ground truth didn't "
        "already cover."
        + (f" {len(failed)} lookup{'s' if len(failed) != 1 else ''} failed." if failed else "")
    )
    with st.expander(f"On-demand agent lookups ({count})"):
        for call in result.agent_tool_calls:
            status = "ok" if call.ok else "failed"
            st.markdown(f"**{call.tool}** — {status}")
            st.caption(f"args: {call.arguments}")
            if call.summary:
                st.caption(call.summary)


def _render_verdicts(result: AnalysisResult) -> None:
    with st.expander(f"Deterministic verdicts — spotlight matchups ({len(result.verdicts)})"):
        st.caption(
            "Supplementary hand-picked matchups (not necessarily ones that "
            "occurred) — see 'Turn-by-turn checks' below for the exhaustive, "
            "ordered ground truth covering every real action of the game."
        )
        if not result.verdicts:
            st.caption("(none selected for this question)")
        for verdict in result.verdicts:
            dmg = verdict.best_damage
            st.markdown(
                f"**{verdict.attacker} → {verdict.defender}** using "
                f"*{verdict.best_move}*: {dmg.min_percent}%–{dmg.max_percent}% "
                f"({dmg.ko_chance_text or 'n/a'})"
            )
            st.caption(dmg.description or "(no spread/nature detail returned)")
            if verdict.speed:
                sp = verdict.speed
                tie = " (speed tie)" if sp.is_tie else ""
                conds = f" — {', '.join(sp.conditions)}" if sp.conditions else ""
                st.caption(
                    f"Moves first: {sp.faster} {sp.faster_speed} "
                    f"> {sp.slower} {sp.slower_speed}{tie}{conds}"
                )
            if verdict.stat_caveat:
                st.warning(f"{icon_md(PIKACHU_ICON)} {verdict.stat_caveat}")


def _render_turn_checks(checks: list[TurnCheck], current_turn: int) -> None:
    with st.expander(
        f"Turn-by-turn checks — every real action, in order ({len(checks)})", expanded=True
    ):
        st.caption(
            "The exhaustive, ordered ground-truth feedback loop: the engine "
            "re-consulted, under the battle state at each move (field, HP, status, "
            "items), for the move actually used, every other confirmed move into "
            "every opposing target, the incoming KO threats, and the Protect / "
            "switch / speed-control options."
        )
        for tc in checks:
            header = f"T{tc.turn} · {tc.actor} used {tc.move}"
            if tc.actor_hp_percent is not None:
                header += f" (at {tc.actor_hp_percent:g}% HP)"
            if tc.conditions:
                header += f" — {', '.join(tc.conditions)}"
            header = f"**{header}**"
            if tc.turn == current_turn:
                header = f":orange-background[{header}]"
            st.markdown(header)
            for d in tc.damage_checks:
                hp = (
                    f" (at {d.target_hp_before_percent:g}% HP)"
                    if d.target_hp_before_percent is not None else ""
                )
                st.caption(
                    f"{d.target}{hp}: projected {d.projected_min_percent}–"
                    f"{d.projected_max_percent}% ({d.projected_ko_text or 'n/a'}) "
                    f"| actual: {d.actual_result or 'n/a'}"
                )
                if d.description:
                    st.caption(f"   {d.description}")
            if tc.speed:
                c = f" — {', '.join(tc.speed.conditions)}" if tc.speed.conditions else ""
                st.caption(f"speed: {tc.speed.faster} moves first{c}")
            if tc.best_alternatives:
                st.markdown("_Best attacking plays this turn (ranked, every target):_")
                for i, alt in enumerate(tc.best_alternatives, start=1):
                    marker = f" {icon_html(PIKACHU_ICON, size=14)}" if alt.move == tc.move else ""
                    st.caption(
                        f"{i}. {alt.move} vs {alt.target}: "
                        f"{alt.min_percent}%–{alt.max_percent}% "
                        f"({alt.ko_chance_text or 'n/a'}){marker}",
                        unsafe_allow_html=True,
                    )
            for threat in tc.incoming_threats:
                if threat.can_ko:
                    order = {True: "moves first", False: "moves after", None: "speed unknown"}
                    st.caption(
                        f"KO threat: {threat.attacker}'s {threat.move} "
                        f"{threat.min_percent}%–{threat.max_percent}% "
                        f"({threat.ko_chance_text or 'n/a'}; {order[threat.moves_first]})"
                    )
            for option in tc.decision_options:
                st.caption(f"{icon_html(POKEBALL_ICON, size=14)} {option.summary}", unsafe_allow_html=True)
            if tc.stat_caveat:
                st.warning(f"{icon_md(PIKACHU_ICON)} {tc.stat_caveat}")
            if tc.note:
                st.caption(tc.note)


def _render_protect_reads(reads: list[ProtectRead], current_turn: int) -> None:
    with st.expander(
        f"Protect reads — precomputed spread/misallocation classification ({len(reads)})"
    ):
        st.caption(
            "One entry per Protect-family block, already classified so the "
            "explanation only has to report it: spread moves are "
            "protect-resistant (not a read); a genuine single-target read "
            "that denied no real threat while a teammate fainted the same "
            "turn is flagged misallocated."
        )
        for pr in reads:
            kind = "genuine read" if pr.is_genuine_read else "spread (not a read)"
            pr_header = f"**T{pr.turn} · {pr.blocker} blocked {pr.attacker}'s {pr.move}** — {kind}"
            if pr.turn == current_turn:
                pr_header = f":orange-background[{pr_header}]"
            st.markdown(pr_header)
            d = pr.value_denied
            st.caption(
                f"Denied: {d.projected_min_percent}–{d.projected_max_percent}% "
                f"({d.projected_ko_text or 'n/a'})"
            )
            if pr.other_targets_hit:
                others = ", ".join(
                    f"{o.target} {o.projected_min_percent}–{o.projected_max_percent}%"
                    for o in pr.other_targets_hit
                )
                st.caption(f"Also hit that turn regardless: {others}")
            if pr.misallocated:
                st.warning(
                    f"{icon_md(PIKACHU_ICON)} Misallocated: no immediate KO "
                    f"threat, but {pr.teammate_fainted} fainted the same turn."
                )
            elif pr.was_immediate_ko_threat:
                st.caption("Justified: denied a same-turn OHKO chance.")
