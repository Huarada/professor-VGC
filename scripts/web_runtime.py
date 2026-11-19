"""Start the local React API after loading verified Windows TLS roots."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.local_runtime import configure_trust


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ProfessorVGC React API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8503)
    args = parser.parse_args()
    configure_trust(windows=os.name == "nt")
    import uvicorn

    uvicorn.run("src.api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
