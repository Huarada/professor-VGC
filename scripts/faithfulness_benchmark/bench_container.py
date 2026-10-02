"""Composition root for benchmark runs, with an optional offline Chaos source.

The app itself reads Chaos data from Firestore only (see ``Container.
chaos_repository``). Benchmark tooling may instead read the SAME raw dumps
from ``data/chaos/`` (the files the migration script uploads), read-only, so
a calibration run never depends on network access or cloud credentials.
"""

from __future__ import annotations

from pathlib import Path

from src.adapters.chaos.chaos_repository import ChaosRepository, ChaosRepositoryLike
from src.services.container import Container

LOCAL_CHAOS_DIR = Path(__file__).resolve().parents[2] / "data" / "chaos"


class LocalChaosContainer(Container):
    """``Container`` whose Chaos repository reads ``data/chaos/`` (read-only)."""

    def chaos_repository(self) -> ChaosRepositoryLike:
        if self._local_chaos is None:
            self._local_chaos = ChaosRepository(
                LOCAL_CHAOS_DIR, reg_fallback_depth=self.settings.reg_fallback_depth
            )
        return self._local_chaos

    _local_chaos: ChaosRepository | None = None


def build_container(chaos_source: str) -> Container:
    """``"firestore"`` (the app's own source) or ``"local"`` (offline dumps)."""
    if chaos_source == "local":
        return LocalChaosContainer()
    if chaos_source == "firestore":
        return Container()
    raise SystemExit(f"--chaos must be 'firestore' or 'local', got {chaos_source!r}")
