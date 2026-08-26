"""Regression: NodeIpcClient must decode the Node subprocess's stdout as
UTF-8, not whatever the OS locale defaults to.

Found live while running scripts/faithfulness_benchmark: on this Windows
machine, `subprocess.Popen(..., text=True)` (no explicit encoding) decodes
with `locale.getpreferredencoding()` — cp1252 here — which cannot represent
several byte values Node's real UTF-8 stdout can legitimately contain (an
accented species name, an "×", …). The reader thread crashed with
UnicodeDecodeError, silently losing that one response — degraded gracefully
(callers already catch CalcEngineError and skip), but for no real reason.
This is an integration test against a REAL Node subprocess (not a fake),
because the bug is specifically in how Python decodes Node's actual stdout
bytes, which no in-memory fake can exercise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.adapters.node_ipc import NodeIpcClient
from src.domain.exceptions import CalcEngineError

_ECHO_SERVER = Path(__file__).resolve().parent / "fixtures" / "echo_unicode_server.js"


@pytest.fixture
def echo_client():
    try:
        client = NodeIpcClient(server_script=_ECHO_SERVER, timeout_seconds=10)
        client.request({"ping": "warmup"})
    except CalcEngineError as exc:  # pragma: no cover - environment without node
        pytest.skip(f"Node unavailable: {exc}")
    yield client
    client.close()


def test_non_cp1252_utf8_bytes_do_not_crash_the_reader_thread(echo_client):
    response = echo_client.request({"ping": "hello"})
    assert response["ping"] == "hello"
    assert response["unicode"] == "a “smart quote” test — é×✓日鴞"
