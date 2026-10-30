"""Launch the project environment without changing another project's interpreter or TLS settings."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def local_environment(project: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Trust an operator-provided local CA in this child process, never bypass TLS."""
    environment = dict(inherited)
    bundle = project / "docker" / "extra-ca-certs" / "local-avast-bundle.pem"
    if bundle.is_file():
        for key in ("REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"):
            environment.setdefault(key, str(bundle))
    return environment


def local_command(project: Path, arguments: Sequence[str], *, windows: bool) -> list[str]:
    """Choose an explicit interpreter; Windows never falls back to a WSL environment."""
    executable = project / (".venv-windows/Scripts/python.exe" if windows else ".venv/bin/python")
    if not executable.is_file():
        name = ".venv-windows" if windows else ".venv"
        raise FileNotFoundError(f"Project environment missing. Create it with: python -m venv {name}")
    return [str(executable), str(project / "scripts/local_runtime.py"), *arguments]


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    try:
        command = local_command(project, sys.argv[1:], windows=os.name == "nt")
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return subprocess.run(command, cwd=project, env=local_environment(project, os.environ), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
