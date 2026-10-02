"""Tests for the pure, network-free logic in sync_smogon_chaos_to_firestore.py:
the current-gen-VGC filename filter, the format filter, plain/gzipped payload
decoding, and month/file-list parsing against
captured Apache-autoindex-shaped HTML fixtures (real structure, confirmed
live against https://www.smogon.com/stats/ before being hardcoded here — not
guessed).
"""

from __future__ import annotations

import gzip
import json

import pytest

from scripts.sync_smogon_chaos_to_firestore import (
    _FILE_RE,
    _MONTH_RE,
    _decode_payload,
    _is_current_gen_vgc,
    _select_formats,
    _tier_id,
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


def test_file_regex_matches_plain_json_listing():
    """From 2026-08 Smogon serves plain ``.json`` files (shape confirmed live
    against https://www.smogon.com/stats/2026-09/chaos/)."""
    html = (
        '<a href="gen9championsvgc2026regmc-1760.json">'
        'gen9championsvgc2026regmc-1760.json</a>                01-Oct-2026 14:40\n'
        '<a href="gen9championsvgc2026regmcbo3-0.json">'
        'gen9championsvgc2026regmcbo3-0.json</a>                01-Oct-2026 14:40\n'
    )
    files = _FILE_RE.findall(html)
    assert files == ["gen9championsvgc2026regmc-1760.json", "gen9championsvgc2026regmcbo3-0.json"]
    assert all(_is_current_gen_vgc(f) for f in files)


def test_tier_id_strips_either_extension():
    assert _tier_id("gen9championsvgc2026regmc-1760.json") == "gen9championsvgc2026regmc-1760"
    assert _tier_id("gen9championsvgc2026regmb-0.json.gz") == "gen9championsvgc2026regmb-0"


def test_decode_payload_reads_plain_and_gzipped_bodies():
    body = json.dumps({"info": {"metagame": "gen9championsvgc2026regmc"}, "data": {}}).encode()
    assert _decode_payload(body)["info"]["metagame"] == "gen9championsvgc2026regmc"
    assert _decode_payload(gzip.compress(body))["info"]["metagame"] == "gen9championsvgc2026regmc"


_MONTH_FILES = [
    "gen9championsvgc2026regmb-0.json",
    "gen9championsvgc2026regmb-1760.json",
    "gen9championsvgc2026regmc-0.json",
    "gen9championsvgc2026regmc-1760.json",
    "gen9championsvgc2026regmcbo3-0.json",
    "gen9championsvgc2026regmcbo3-1760.json",
]


def test_select_formats_keeps_everything_without_a_filter():
    assert _select_formats(_MONTH_FILES, None) == _MONTH_FILES


def test_select_formats_matches_exact_format_ids_only():
    """``regmc`` must not also pull ``regmcbo3`` (and vice versa)."""
    assert _select_formats(_MONTH_FILES, ["gen9championsvgc2026regmc"]) == [
        "gen9championsvgc2026regmc-0.json",
        "gen9championsvgc2026regmc-1760.json",
    ]
    both = _select_formats(
        _MONTH_FILES, ["gen9championsvgc2026regmc", "gen9championsvgc2026regmcbo3"]
    )
    assert both == _MONTH_FILES[2:]


def test_select_formats_fails_loudly_on_an_unpublished_format():
    with pytest.raises(SystemExit, match="gen9championsvgc2026regmd"):
        _select_formats(_MONTH_FILES, ["gen9championsvgc2026regmc", "gen9championsvgc2026regmd"])
