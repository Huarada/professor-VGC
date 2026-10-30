"""Child-only runtime bootstrap: use the Windows trust store before loading network SDKs."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def configure_trust(*, windows: bool) -> None:
    """Keep certificate verification while honoring operator-installed Windows roots."""
    if windows:
        import truststore

        truststore.inject_into_ssl()


def main() -> None:
    configure_trust(windows=os.name == "nt")
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    sys.argv = ["streamlit", "run", str(project / "src/ui/app.py"), *sys.argv[1:]]
    runpy.run_module("streamlit", run_name="__main__")


if __name__ == "__main__":
    main()
