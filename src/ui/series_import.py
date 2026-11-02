"""Import a BO3 in one action, before quota consumption and model calls."""

import streamlit as st

from src.domain.exceptions import ProfessorVGCError
from src.services.container import Container
from src.ui.loading import loading_overlay


def _import(container: Container) -> None:
    try:
        with loading_overlay(title="Importing your BO3", steps=("Reading available public game logs...",)):
            result = container.import_bo3_replays(st.session_state.get("bo3_import_source", ""))
    except ProfessorVGCError as exc:
        st.session_state["bo3_import_error"] = str(exc)
        return
    by_number = {game.number: game.payload for game in result.games}
    for number in range(1, 4):
        st.session_state[f"bo3_source_{number}"] = by_number.get(number, "")
    st.session_state["bo3_import_receipt"] = result
    st.session_state["bo3_teamsheet"] = result.teamsheet.model_dump_json(indent=2) if result.teamsheet else ""
    for key in ("bo3_result", "bo3_replays", "bo3_error", "bo3_import_error"):
        st.session_state.pop(key, None)


def render_series_import(container: Container) -> None:
    """A callback updates game widgets before their next render."""
    st.text_area("BO3 room URL, any game replay, or multiple replay links", key="bo3_import_source", height=90,
                 placeholder="https://play.pokemonshowdown.com/game-bestof3-...",
                 help="Paste one series/game URL or all replay URLs together. Official game numbers determine order.")
    st.button("Import BO3", on_click=_import, args=(container,))
    st.caption("Import reads published replays or available public finished battle logs. It does not upload replays, "
               "use an analysis slot or call a model. A 2-0 BO3 needs only two games.")
    if st.session_state.get("bo3_import_error"):
        st.error(st.session_state["bo3_import_error"])
    receipt = st.session_state.get("bo3_import_receipt")
    if receipt is not None:
        st.success(f"Imported {len(receipt.games)} game(s). Review the games below, then analyze the series.")
        for game in receipt.games:
            label = "Public finished battle" if game.acquisition == "public_finished_room" else "Replay"
            st.markdown(f"Game {game.number}: [{label}]({game.source_url or game.location.url})")
        if receipt.teamsheet and receipt.teamsheet.pokemon:
            first = [entry for entry in receipt.teamsheet.pokemon if entry.available_from_game == 1]
            st.caption(f"Public teamsheets retrieved: {len(first)} Pokemon, "
                       f"{sum(entry.nature is not None for entry in first)} declared natures. "
                       "Hidden stat points remain unknown and are searched from damage evidence.")
        for warning in receipt.warnings:
            st.warning(warning)
