"""Download official Smogon Chaos tiers for benchmark runs (git-ignored cache).

    python -m scripts.faithfulness_benchmark.chaos_corpus \
        [--month 2026-09] [--format gen9championsvgc2026regmb ...] \
        [--cutoffs 0 1500 1630 1760] [--out data/chaos-cache]

Fetches ``https://www.smogon.com/stats/<month>/chaos/<format>-<cutoff>.json``
and keeps a file only when its own ``info.metagame`` matches the format it was
requested as — the same integrity rule the repositories enforce.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data" / "chaos-cache"
_STATS = "https://www.smogon.com/stats"


def download(month: str, formats: list[str], cutoffs: list[int], out: Path) -> list[str]:
    """Saved file names (skipping ones already present)."""
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "ProfessorVGC-benchmark (+https://github.com/Huarada/professor-VGC)"
    saved: list[str] = []
    for format_id in formats:
        for cutoff in cutoffs:
            name = f"{format_id}-{cutoff}.json"
            target = out / name
            if target.exists():
                saved.append(name)
                continue
            response = session.get(f"{_STATS}/{month}/chaos/{name}", timeout=300)
            if not response.ok:
                print(f"skip {name}: HTTP {response.status_code}")
                continue
            declared = json.loads(response.text).get("info", {}).get("metagame")
            if declared != format_id:
                print(f"skip {name}: contains {declared!r} data")
                continue
            target.write_text(response.text, encoding="utf-8")
            saved.append(name)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="2026-09")
    parser.add_argument("--format", nargs="+", default=["gen9championsvgc2026regmb"])
    parser.add_argument("--cutoffs", nargs="+", type=int, default=[0, 1500, 1630, 1760])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    saved = download(args.month, args.format, args.cutoffs, args.out)
    print(f"{len(saved)} tiers in {args.out}: {saved}")


if __name__ == "__main__":
    main()
