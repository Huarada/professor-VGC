"""Calibrate the deterministic layer against REAL battle logs (no LLM, no API key).

    python -m scripts.faithfulness_benchmark.run_engine_calibration \
        [--replays data/replays/cache] [--limit N] [--chaos firestore|local] [--out FILE.json]

For each replay: parse it with the product's parser, run the per-turn
re-checks with the real ``@smogon/calc`` engine and the real Chaos data
(Firestore, as configured; ``--chaos local`` reads the same dumps from
``data/chaos/`` offline), then compare every projected damage range with
the damage the log shows actually happened (``observed_damage.py``, an
independent reader of the raw log). See ``engine_calibration.py`` for the
judging rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from scripts.faithfulness_benchmark.bench_container import build_container
from scripts.faithfulness_benchmark.engine_calibration import CalibrationReport, calibrate
from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
from scripts.faithfulness_benchmark.replay_corpus import DEFAULT_CACHE_DIR, load_replays
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.models import TurnCheck
from src.services.battle_context import context_species
from src.services.container import Container
from src.services.turn_simulator import TurnReplaySimulator


def deterministic_checks(container: Container, replay: dict[str, object]) -> list[TurnCheck]:
    """The pipeline's per-turn re-checks for one replay, with no LLM involved."""
    game_state = ShowdownReplayParser().parse(replay)
    meta = container.chaos().build_match_context(
        context_species(game_state), metagame=game_state.format_id, rating=game_state.rating
    )
    simulator = TurnReplaySimulator(container.calc_engine(), container.settings.calc_gen)
    return simulator.simulate(game_state, meta)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--chaos", default="firestore", help="firestore (default) | local")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    replays = load_replays(args.replays, limit=args.limit)
    if not replays:
        raise SystemExit(
            f"No replays in {args.replays}. Download some first: "
            "python -m scripts.faithfulness_benchmark.replay_corpus --count 40"
        )
    container = build_container(args.chaos)
    overall = CalibrationReport()
    per_replay: dict[str, dict[str, object]] = {}
    try:
        for replay_id, replay in replays.items():
            report = calibrate(
                extract_observed_hits(str(replay["log"])), deterministic_checks(container, replay)
            )
            overall.hits.extend(report.hits)
            per_replay[replay_id] = report.summary()
            print(f"{replay_id}: {report.count('correct')}/"
                  f"{report.count('correct') + report.count('incorrect')} within range")
    finally:
        container.shutdown()

    summary = overall.summary()
    print(json.dumps(summary, indent=2))
    if args.out:
        misses = [
            {"turn": h.hit.turn, "attacker": h.hit.attacker, "move": h.hit.move,
             "defender": h.hit.defender, "observed": h.hit.damage, "ko": h.hit.fainted,
             "spread": h.hit.spread, "projected": [h.projected_min, h.projected_max],
             "reason": h.reason}
            for h in overall.hits if h.outcome == "incorrect"
        ]
        args.out.write_text(
            json.dumps({"summary": summary, "per_replay": per_replay, "misses": misses}, indent=2),
            encoding="utf-8",
        )
        print(f"saved {args.out}")


if __name__ == "__main__":
    main()
