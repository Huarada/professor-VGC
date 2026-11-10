"""Launch the project environment without changing another project's interpreter or TLS settings."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values


def local_environment(project: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Trust an operator-provided local CA in this child process, never bypass TLS."""
    environment = dict(inherited)
    bundle = project / "docker" / "extra-ca-certs" / "local-avast-bundle.pem"
    key = "PROFESSORVGC_FIRESTORE_GRPC_CA_BUNDLE_PATH"
    configured = environment.get(key)
    if configured is None:
        configured = dotenv_values(project / ".env").get(key)
    # gRPC uses its own native trust store. Resolve the application's explicit
    # CA before SDK imports; the optional local fallback must not shadow it.
    grpc_bundle = configured or (str(bundle) if bundle.is_file() else None)
    if grpc_bundle:
        environment.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", grpc_bundle)
    if bundle.is_file():
        for transport_key in ("REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
            environment.setdefault(transport_key, str(bundle))
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
