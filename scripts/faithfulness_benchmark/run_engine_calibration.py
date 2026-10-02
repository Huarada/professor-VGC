"""Calibrate the deterministic layer against REAL battle logs (no LLM, no API key).

    python -m scripts.faithfulness_benchmark.run_engine_calibration \
        [--replays data/replays/cache] [--limit N] [--chaos firestore|local] \
        [--relative-tolerance 0.05] [--no-ev-envelope] [--out FILE.json]

For each replay: parse it with the product's parser, run the per-turn
re-checks with the real ``@smogon/calc`` engine and the real Chaos data
(Firestore, as configured; ``--chaos local`` reads the same dumps from
``data/chaos/`` offline), then compare every projected damage range with
the damage the log shows actually happened (``observed_damage.py``, an
independent reader of the raw log). Rates come with 95% intervals, a
sensitivity table across tolerances, and — unless disabled — how many misses
EV/nature variance alone explains (``ev_envelope.py``). See
``engine_calibration.py`` for the judging rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from scripts.faithfulness_benchmark.bench_container import build_container
from scripts.faithfulness_benchmark.engine_calibration import CalibrationReport, calibrate
from scripts.faithfulness_benchmark.ev_envelope import RecordingCalc, envelopes_for
from scripts.faithfulness_benchmark.observed_damage import extract_observed_hits
from scripts.faithfulness_benchmark.replay_corpus import DEFAULT_CACHE_DIR, load_replays
from scripts.faithfulness_benchmark.tolerance import ToleranceBand
from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.domain.interfaces import CalcEngineAdapter
from src.domain.models import TurnCheck
from src.services.battle_context import context_species
from src.services.container import Container
from src.services.turn_simulator import TurnReplaySimulator


def deterministic_checks(
    container: Container, replay: dict[str, Any], engine: CalcEngineAdapter | None = None
) -> list[TurnCheck]:
    """The pipeline's per-turn re-checks for one replay, with no LLM involved."""
    game_state = ShowdownReplayParser().parse(replay)
    meta = container.chaos().build_match_context(
        context_species(game_state), metagame=game_state.format_id, rating=game_state.rating
    )
    simulator = TurnReplaySimulator(engine or container.calc_engine(), container.settings.calc_gen)
    return simulator.simulate(game_state, meta)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--chaos", default="firestore", help="firestore (default) | local")
    parser.add_argument("--relative-tolerance", type=float, default=0.05,
                        help="relative widening of each projected bound (default 0.05 = 5%%)")
    parser.add_argument("--no-ev-envelope", action="store_true",
                        help="skip the EV/nature envelope (2 extra calcs per hit)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    replays = load_replays(args.replays, limit=args.limit)
    if not replays:
        raise SystemExit(
            f"No replays in {args.replays}. Download some first: "
            "python -m scripts.faithfulness_benchmark.replay_corpus --count 40"
        )
    band = ToleranceBand(relative=args.relative_tolerance)
    container = build_container(args.chaos)
    overall = CalibrationReport(band=band)
    per_replay: dict[str, dict[str, object]] = {}
    try:
        engine = container.calc_engine()
        for replay_id, replay in replays.items():
            recorder = RecordingCalc(engine)
            checks = deterministic_checks(container, replay, recorder)
            hits = extract_observed_hits(str(replay["log"]))
            envelopes = None if args.no_ev_envelope else envelopes_for(hits, recorder, engine)
            report = calibrate(hits, checks, band, envelopes)
            overall.hits.extend(report.hits)
            per_replay[replay_id] = {
                "judged": report.count("correct") + report.count("incorrect"),
                "correct": report.count("correct"),
            }
            print(f"{replay_id}: {report.count('correct')}/"
                  f"{report.count('correct') + report.count('incorrect')} within {band.label}")
    finally:
        container.shutdown()

    summary = overall.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        misses = [
            {"turn": h.hit.turn, "attacker": h.hit.attacker, "move": h.hit.move,
             "defender": h.hit.defender, "observed": h.hit.damage, "ko": h.hit.fainted,
             "spread": h.hit.spread, "projected": [h.projected_min, h.projected_max],
             "ev_envelope": list(h.ev_envelope) if h.ev_envelope else None,
             "explained_by_ev_nature": h.within_ev_envelope, "reason": h.reason}
            for h in overall.hits if h.outcome == "incorrect"
        ]
        args.out.write_text(
            json.dumps({"summary": summary, "per_replay": per_replay, "misses": misses},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"saved {args.out}")


if __name__ == "__main__":
    main()
