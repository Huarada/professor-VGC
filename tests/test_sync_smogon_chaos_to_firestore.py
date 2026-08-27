"""Tests for the pure, network-free logic in sync_smogon_chaos_to_firestore.py:
the current-gen-VGC filename filter, and month/file-list parsing against
captured Apache-autoindex-shaped HTML fixtures (real structure, confirmed
live against https://www.smogon.com/stats/ before being hardcoded here — not
guessed).
"""

from __future__ import annotations

from scripts.sync_smogon_chaos_to_firestore import (
    _FILE_RE,
    _MONTH_RE,
    _is_current_gen_vgc,
)


def test_is_current_gen_vgc_accepts_gen9_vgc_formats():
    assert _is_current_gen_vgc("gen9championsvgc2026regmb-1760.json.gz")
    assert _is_current_gen_vgc("gen9championsvgc2026regmbbo3-0.json.gz")  # Bo3 variant
    assert _is_current_gen_vgc("gen9vgc2025regh-1500.json.gz")


def test_is_current_gen_vgc_rejects_legacy_generation_vgc_formats():
    """Smogon's stats archive goes back to 2014 and still lists VGC formats
    from old generations (gen4vgc2010, gen6vgc2015, ...) — this project is
    gen9-only (CLAUDE.md, PROFESSORVGC_CALC_GEN=9); a naive "vgc" substring
    check alone would wrongly pull these in."""
    assert not _is_current_gen_vgc("gen4vgc2010-1500.json.gz")
    assert not _is_current_gen_vgc("gen6vgc2015-1760.json.gz")


def test_is_current_gen_vgc_rejects_non_vgc_gen9_formats():
    assert not _is_current_gen_vgc("gen9ou-1825.json.gz")
    assert not _is_current_gen_vgc("gen9randombattle-0.json.gz")


def test_month_regex_matches_real_apache_autoindex_shape():
    """HTML shape confirmed live against https://www.smogon.com/stats/."""
    html = (
        '<a href="2026-06/">2026-06/</a>  01-Jul-2026 00:00  -\n'
        '<a href="2026-07/">2026-07/</a>  01-Aug-2026 00:00  -\n'
        '<a href="2020-11-H1/">2020-11-H1/</a>  01-Dec-2020 00:00  -\n'  # half-year variant, must NOT match
    )
    months = _MONTH_RE.findall(html)
    assert months == ["2026-06", "2026-07"]
    assert max(months) == "2026-07"


def test_file_regex_matches_real_apache_autoindex_shape():
    """HTML shape confirmed live against
    https://www.smogon.com/stats/2026-07/chaos/."""
    html = (
        '<a href="gen9ou-1825.json.gz">gen9ou-1825.json.gz</a>  '
        '01-Aug-2026 13:17  54188\n'
        '<a href="gen9championsvgc2026regmb-1760.json.gz">'
        'gen9championsvgc2026regmb-1760.json.gz</a>  01-Aug-2026 13:17  5441264\n'
        '<a href="../">../</a>\n'
    )
    files = _FILE_RE.findall(html)
    assert files == ["gen9ou-1825.json.gz", "gen9championsvgc2026regmb-1760.json.gz"]
