"""A corpus of REAL public Showdown replays for the log-grounded benchmark.

    python -m scripts.faithfulness_benchmark.replay_corpus \
        [--format gen9championsvgc2026regmb] [--count 40] [--out data/replays/cache]

Downloads the most recent public replays of a format from
replay.pokemonshowdown.com (the same public JSON the app's replay-URL feature
reads) into a git-ignored cache. Engineered fixtures are useful for unit
tests, but only real games can show whether the analysis matches reality.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "replays" / "cache"
DEFAULT_FORMAT = "gen9championsvgc2026regmb"
_API = "https://replay.pokemonshowdown.com"
_USER_AGENT = "ProfessorVGC-benchmark (+https://github.com/Huarada/professor-VGC)"
_POLITE_DELAY_SECONDS = 0.5


def load_replays(directory: Path, limit: int | None = None) -> dict[str, dict[str, Any]]:
    """Every ``*.json`` replay (with a ``log``) in ``directory``, by id, sorted."""
    replays: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("log"), str):
            replays[str(data.get("id") or path.stem)] = data
        if limit is not None and len(replays) >= limit:
            break
    return replays


def download(format_id: str, count: int, out: Path) -> int:
    """Fetch up to ``count`` recent public replays of ``format_id`` into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT
    ids: list[str] = []
    before: int | None = None
    while len(ids) < count:
        params: dict[str, Any] = {"format": format_id}
        if before is not None:
            params["before"] = before
        page = session.get(f"{_API}/search.json", params=params, timeout=20).json()
        if not page:
            break
        ids.extend(entry["id"] for entry in page if not entry.get("private"))
        before = page[-1]["uploadtime"]
        time.sleep(_POLITE_DELAY_SECONDS)
    saved = 0
    for replay_id in ids[:count]:
        target = out / f"{replay_id}.json"
        if target.exists():
            saved += 1
            continue
        response = session.get(f"{_API}/{replay_id}.json", timeout=20)
        if response.ok:
            target.write_text(response.text, encoding="utf-8")
            saved += 1
        time.sleep(_POLITE_DELAY_SECONDS)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", default=DEFAULT_FORMAT)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--out", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()
    print(f"saved {download(args.format, args.count, args.out)} replays to {args.out}")


if __name__ == "__main__":
    main()
