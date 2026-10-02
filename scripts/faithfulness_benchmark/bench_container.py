"""Composition root for benchmark runs, with an optional offline Chaos source.

The app itself reads Chaos data from Firestore only (see ``Container.
chaos_repository``). Benchmark tooling may instead read Chaos dumps from a
local directory, read-only, so a calibration run never depends on network
access or cloud credentials:

- ``--chaos local``: ``data/chaos/`` (note: the bundled files there are
  singles BSS data under VGC names and are rejected by the repository's
  integrity check — see DATA.md);
- ``--chaos <directory>``: any directory of dumps, e.g. the official VGC tiers
  downloaded by ``chaos_corpus.py`` into ``data/chaos-cache/``.
"""

from __future__ import annotations

from pathlib import Path

from src.adapters.chaos.chaos_repository import ChaosRepository, ChaosRepositoryLike
from src.services.container import Container

LOCAL_CHAOS_DIR = Path(__file__).resolve().parents[2] / "data" / "chaos"


class LocalChaosContainer(Container):
    """``Container`` whose Chaos repository reads a local directory (read-only)."""

    chaos_dir: Path = LOCAL_CHAOS_DIR
    _local_chaos: ChaosRepository | None = None

    def chaos_repository(self) -> ChaosRepositoryLike:
        if self._local_chaos is None:
            self._local_chaos = ChaosRepository(
                self.chaos_dir, reg_fallback_depth=self.settings.reg_fallback_depth
            )
        return self._local_chaos


def build_container(chaos_source: str) -> Container:
    """``"firestore"`` (the app's own source), ``"local"`` (``data/chaos/``)
    or a path to a directory of Chaos dumps."""
    if chaos_source == "firestore":
        return Container()
    container = LocalChaosContainer()
    if chaos_source != "local":
        directory = Path(chaos_source)
        if not directory.is_dir():
            raise SystemExit(f"--chaos must be 'firestore', 'local' or a directory, got {chaos_source!r}")
        container.chaos_dir = directory
    return container
