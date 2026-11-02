"""Shared monochrome loading overlay lifecycle for BO3 acquisition and analysis."""

from unittest.mock import Mock, patch

import pytest

from src.domain.exceptions import ReplayFetchError
from src.ui.battle_panel import PLACEHOLDER_SPRITE
from src.ui.loading import loading_overlay, loading_overlay_html
from src.ui.series_import import _import


@pytest.mark.parametrize("failed", [False, True])
def test_shared_overlay_is_removed_on_success_and_error(failed):
    placeholder = Mock()
    with patch("streamlit.empty", return_value=placeholder):
        try:
            with loading_overlay(title="Analysing your BO3", steps=("Checking evidence...",)):
                if failed:
                    raise ReplayFetchError("Offline failure")
        except ReplayFetchError:
            assert failed
    placeholder.empty.assert_called_once()
    rendered = placeholder.markdown.call_args.args[0]
    assert PLACEHOLDER_SPRITE in rendered and "Analysing your BO3" in rendered


def test_import_failure_uses_the_same_ball_and_leaves_no_stuck_overlay():
    container = Mock()
    container.import_bo3_replays.side_effect = ReplayFetchError("Room expired")
    state = {"bo3_import_source": "room"}
    placeholder = Mock()
    with patch("streamlit.session_state", state), patch("streamlit.empty", return_value=placeholder):
        _import(container)
    assert state["bo3_import_error"] == "Room expired"
    assert "Importing your BO3" in placeholder.markdown.call_args.args[0]
    placeholder.empty.assert_called_once()


def test_custom_overlay_text_is_escaped_and_bo1_defaults_are_preserved():
    custom = loading_overlay_html(title="<BO3>", steps=("<reading>",))
    assert "&lt;BO3&gt;" in custom and "&lt;reading&gt;" in custom
    assert "Analysing your battle" in loading_overlay_html()
    assert PLACEHOLDER_SPRITE in loading_overlay_html()


def test_actual_stage_caption_stays_visible_and_wraps_long_operation_details():
    rendered = loading_overlay_html(steps=("Stage 1 of 3 — Game 2 of 3: comparing Dragon Claw damage...",))
    assert 'data-live-stage="true"' in rendered
    caption = rendered.split('data-live-stage="true"')[1].split("</span>")[0]
    assert "Stage 1 of 3" in caption and "animation" not in caption
    assert 'aria-live="polite"' in rendered and "height:auto" in rendered
    assert "calc(100vw - 40px)" in rendered
