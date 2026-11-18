"""The simulator generator must emit valid, reproducible, fully known Champions series."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.bo3_truth_corpus import STATS, discover

TOOL = Path(__file__).resolve().parents[1] / "tools" / "bo3_sim"
pytestmark = pytest.mark.skipif(
    not (TOOL / "node_modules" / "pokemon-showdown").is_dir() or shutil.which("node") is None,
    reason="run `npm install` in tools/bo3_sim to enable the simulator corpus",
)


def generate(out: Path, seed: int, count: int = 1) -> None:
    subprocess.run(["node", "generate.js", "--count", str(count), "--seed", str(seed), "--out", str(out)],
                   cwd=TOOL, check=True, capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def series(tmp_path_factory):
    out = tmp_path_factory.mktemp("sim")
    generate(out, 11)
    return out


def test_a_series_is_discovered_as_a_public_truth_pair(series):
    assert list(discover(series)) == ["sim11x0"]


def test_truth_gives_both_teams_legal_native_spreads(series):
    teams = json.loads(next(series.glob("bo3-truth-*.json")).read_text(encoding="utf-8"))["teams"]
    assert len(teams) == 2
    for team in teams.values():
        assert len(team) == 6
        for known in team.values():
            assert set(known["points"]) == set(STATS)
            assert sum(known["points"].values()) == 66 and max(known["points"].values()) <= 32
            assert known["nature"]


def test_public_logs_expose_team_sheets_but_never_the_spreads(series):
    games = json.loads(next(series.glob("bo3-public-*.json")).read_text(encoding="utf-8"))["games"]
    assert 2 <= len(games) <= 3
    for game in games:
        assert game["log"].count("|showteam|") == 2 and "|win|" in game["log"]
        assert "evs" not in game["log"].lower()


def test_series_stops_at_two_wins_and_swaps_sides_without_renaming_players(series):
    games = json.loads(next(series.glob("bo3-public-*.json")).read_text(encoding="utf-8"))["games"]
    assert {name for game in games for name in game["players"]} == {"SimAlpha", "SimBeta"}
    wins = [game["log"].split("|win|")[1].splitlines()[0] for game in games]
    assert max(wins.count(name) for name in set(wins)) == 2


def test_the_same_seed_reproduces_the_same_series(series, tmp_path):
    generate(tmp_path, 11)
    for name in ("bo3-public-sim11x0.json", "bo3-truth-sim11x0.json"):
        assert (tmp_path / name).read_bytes() == (series / name).read_bytes()


def test_different_seeds_draw_different_spreads(series, tmp_path):
    generate(tmp_path, 12)
    assert (tmp_path / "bo3-truth-sim12x0.json").read_bytes() != (series / "bo3-truth-sim11x0.json").read_bytes()
