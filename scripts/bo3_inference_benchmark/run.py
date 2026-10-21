"""Run the offline BO3 testbench and write a JSON report; failures exit nonzero."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from src.adapters.calc.candidate_projection import SmogonCandidateProjectionAdapter
from src.adapters.node_ipc import NodeIpcClient
from src.domain.exceptions import ProfessorVGCError

from .fixtures import Corpus, generate
from .runner import run

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "docs" / "bo3-inference" / "validation" / "fixtures.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generate-fixtures", action="store_true",
                        help="Explicitly replace the corpus with engine-generated synthetic inputs before running")
    args = parser.parse_args()
    adapter = SmogonCandidateProjectionAdapter(ipc=NodeIpcClient(
        server_script=ROOT / "node_calc" / "calc_server.js", timeout_seconds=15))
    try:
        if args.generate_fixtures:
            corpus = generate(adapter)
            args.corpus.parent.mkdir(parents=True, exist_ok=True)
            args.corpus.write_text(corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")
        else:
            corpus = Corpus.model_validate_json(args.corpus.read_text(encoding="utf-8"))
        report = run(corpus, adapter)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], **report["summary"]}, indent=2))
        return 0 if report["passed"] else 1
    except (ProfessorVGCError, ValidationError, OSError, ValueError) as exc:
        print(f"BO3 benchmark failed: {exc}", file=sys.stderr)
        return 2
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
