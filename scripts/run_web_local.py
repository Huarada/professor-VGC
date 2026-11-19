"""Launch the React API in the project environment without changing global TLS."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_local import local_environment


def web_local_command(project: Path, arguments: list[str], *, windows: bool) -> list[str]:
    executable = project / (".venv-windows/Scripts/python.exe" if windows else ".venv/bin/python")
    if not executable.is_file():
        name = ".venv-windows" if windows else ".venv"
        raise FileNotFoundError(f"Project environment missing. Create it with: python -m venv {name}")
    return [str(executable), str(project / "scripts/web_runtime.py"), *arguments]


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    try:
        command = web_local_command(project, sys.argv[1:], windows=os.name == "nt")
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return subprocess.run(command, cwd=project, env=local_environment(project, os.environ), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
