"""Generic Node IPC client (one JSON request per line -> one JSON response).

Shared transport for Node subsystems (the @smogon/calc engine and the
@pkmn/smogon dex worker). A single long-lived Node process is reused; access is
serialized because the stdin/stdout protocol is strictly request/response.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from src.domain.exceptions import CalcEngineError


class NodeIpcClient:
    """Launches a Node script and exchanges one JSON line per call."""

    def __init__(
        self,
        server_script: str | Path,
        node_binary: str = "node",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._script = Path(server_script).resolve()
        self._node_binary = node_binary
        self._timeout = float(timeout_seconds)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        if not self._script.exists():
            raise CalcEngineError(f"Node script not found: {self._script}")

    def _ensure(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        try:
            self._process = subprocess.Popen(  # noqa: S603 - trusted local script
                [self._node_binary, str(self._script)],
                cwd=str(self._script.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Node's stdout is UTF-8 regardless of platform. `text=True`
                # alone decodes with `locale.getpreferredencoding()`, which on
                # Windows defaults to a codepage (e.g. cp1252) that cannot
                # represent every UTF-8 codepoint — a calc description
                # containing one (an accented species/move name, an "×", …)
                # crashes the reader thread with UnicodeDecodeError, silently
                # losing that one response (any per-item caller already
                # catches CalcEngineError and skips it, so the pipeline
                # degrades rather than crashing, but the response is lost for
                # no reason). Force the encoding Node actually uses instead of
                # trusting the OS locale.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise CalcEngineError(
                f"Failed to launch Node process ({self._node_binary}): {exc}"
            ) from exc
        return self._process

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return the decoded response dict."""
        with self._lock:
            process = self._ensure()
            if process.stdin is None or process.stdout is None:  # pragma: no cover
                raise CalcEngineError("Node process has no stdio pipes")
            try:
                process.stdin.write(json.dumps(payload) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._reap()
                raise CalcEngineError(f"Node pipe broken: {exc}") from exc
            line = self._read_line(process)
        if not line:
            self._reap()
            raise CalcEngineError("Empty response from Node process")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalcEngineError(f"Invalid JSON from Node process: {line!r}") from exc
        if not isinstance(data, dict):
            raise CalcEngineError(f"Node process returned a non-object response: {line!r}")
        return data

    def _read_line(self, process: subprocess.Popen[str]) -> str:
        result: dict[str, str] = {}

        def _reader() -> None:
            assert process.stdout is not None
            result["line"] = process.stdout.readline()

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(self._timeout)
        if thread.is_alive():
            self._reap()
            raise CalcEngineError(f"Node process timed out after {self._timeout:.1f}s")
        return result.get("line", "")

    def _reap(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:  # pragma: no cover
                pass
            self._process = None

    def close(self) -> None:
        with self._lock:
            if self._process is not None:
                try:
                    if self._process.stdin is not None:
                        self._process.stdin.close()
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
                    self._reap()
                finally:
                    self._process = None
