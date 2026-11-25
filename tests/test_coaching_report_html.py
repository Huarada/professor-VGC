"""The human-validation HTML report: formatting, escaping and what each card carries."""

from __future__ import annotations

from typing import Any

from scripts.coaching_report_html import group_of, markdown_to_html, render_html


def _row(**override: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "bo1-x-turning", "mode": "bo1", "question": "q?", "source": "recorded replay", "seconds": 12.5, "chars": 40,
        "answer": "### Result\nPlayer A won.\n\n- **Kingambit** fainted on turn 11.\n- Another point", "game_answers": [],
        "contradictions": [], "damage_claims": [{"verdict": "correct"}, {"verdict": "incorrect"}], "warnings": [],
        "figures_outside_evidence": [], "validation_rejections": [], "fallback_notice": False,
        "presents_turning_point": True,
        "turning_point": {"turn": 11, "rule": "lead_held_to_the_end", "summary": "After turn 11 Player A led.",
                          "candidates": [{"turn": 11, "winner_alive": 2, "loser_alive": 1, "lead": 0.92, "faints": ["p1 X fainted"]}]},
        "judge": {"grounded": 4, "actionable": 3, "specific": 5, "consistent": 2, "honest": 3, "issues": ["wrong turn"]},
        "replays": [{"label": "The game", "id": "gen9championsvgc2026regmb-1", "players": ["Player A", "Player B"],
                     "log": "|turn|1\n|move|p1a: X|Tackle", "timeline": "Turn 1:\n  - p1 X used Tackle",
                     "url": "https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-1"}]}
    return {**row, **override}


def test_markdown_covers_headings_emphasis_lists_and_paragraphs() -> None:
    out = markdown_to_html("### Result\nA **won** with `code` and *style*.\n\n1. first\n2. second\n\n- a\n  - nested\n\n> quote")
    assert "<h5>Result</h5>" in out and "<strong>won</strong>" in out and "<code>code</code>" in out and "<em>style</em>" in out
    assert out.count("<ol>") == 1 and out.count("<ul>") == 2 and out.count("<li>") == 4 and out.count("</li>") == 4 and "<blockquote>quote</blockquote>" in out


def test_answer_text_is_escaped_not_executed() -> None:
    out = markdown_to_html("<script>alert(1)</script> & **bold**")
    assert "<script>" not in out and "&lt;script&gt;" in out and "<strong>bold</strong>" in out


def test_report_carries_answer_checks_replay_and_a_verdict_box() -> None:
    report = {"provider": "openai", "model": "m", "date": "2026-11-24", "commit": "abc", "rows": [_row()]}
    page = render_html(report)
    assert "<h5>Result</h5>" in page and "Engine turning point: turn 11" in page and "1 correct" in page.replace("damage claims: ", "")
    assert "Engine timeline" in page and "Raw battle log" in page and "download replay" in page
    assert 'href="https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-1"' in page
    assert 'name="v-0"' in page and "Export my verdicts" in page and "wrong turn" in page


def test_group_filters_errors_and_script_in_a_replay_log_are_safe() -> None:
    rows = [_row(), _row(id="bo1sim-a-g1-turning", replays=[{"label": "The game", "id": "", "players": [], "url": "",
                                                          "log": "</pre><script>x</script>", "timeline": "t"}]),
            {"id": "bo3-s-coach_series", "mode": "bo3", "error": "boom <b>"}]
    page = render_html({"provider": "openai", "model": "m", "date": "d", "commit": "c", "rows": rows})
    assert group_of("bo1sim-x") == "bo1-sim" and group_of("bo3-x") == "bo3" and group_of("bo1-x") == "bo1"
    assert 'data-filter="bo1-sim"' in page and 'data-filter="bo3"' in page and "Error: boom &lt;b&gt;" in page
    assert "<script>x</script>" not in page and "&lt;/pre&gt;&lt;script&gt;" in page


def test_replay_links_sit_right_under_the_title_before_the_answer() -> None:
    page = render_html({"provider": "openai", "model": "m", "date": "d", "commit": "c", "rows": [_row()]})
    assert page.index('class="rbar"') < page.index('class="question"') < page.index('class="answer"')
    assert 'class="btn primary" href="https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-1"' in page
    assert "watch in Showdown viewer" in page and 'href="#tl-0-0"' in page and 'id="tl-0-0"' in page


def test_a_simulated_game_has_no_showdown_link_but_keeps_the_viewer_and_the_timeline() -> None:
    sim = _row(id="bo1sim-a-g1-turning", replays=[{"label": "The game", "id": "gen9championsvgc2026regmcbo3-sim700x0g1",
                                                   "players": [], "url": "", "log": "|turn|1", "timeline": "t"}])
    page = render_html({"provider": "openai", "model": "m", "date": "d", "commit": "c", "rows": [sim]})
    assert "simulated game, no Showdown page" in page and "replay.pokemonshowdown.com" not in page
    assert "data-viewer=" in page and "engine timeline" in page


def test_the_viewer_page_embeds_the_log_safely_in_showdowns_replay_shape() -> None:
    from scripts.coaching_report_html import viewer_page

    page = viewer_page({"log": "|turn|1\n</script><b>x</b>"})
    assert 'class="battle-log-data"' in page and "replay-embed.js" in page and page.count("</script>") == 2
    assert "|turn|1" in page and "</script><b>" not in page
