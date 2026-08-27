"""Chaos file repository — rating tiers and regulation-fallback resolution.

Smogon publishes one Chaos file per (metagame, rating cutoff). File names look
like ``gen9championsvgc2026regmb-1760.json`` where the trailing number is the
rating cutoff. This repository indexes a directory of such files and resolves,
for a given match:

* the IDEAL tier   — the highest rating cutoff available for the metagame
  (best-players' sets/strategies, used as the aspirational suggestion);
* the CURRENT tier — the bracket the match's rating falls into
  (cutoff <= rating < next cutoff);
* a REGULATION-FALLBACK chain — when a species is absent from the current
  regulation, older regulations of the SAME game (same franchise, e.g.
  Champions stays in Champions) are tried, nearest first, up to a depth limit.

Only file discovery and selection live here; per-Pokemon extraction stays in the
adapters that consume this repository. Tier selection itself (ideal/current/
regulation-fallback) is storage-agnostic and lives in ``chaos_tier_index.py``,
shared verbatim with ``FirestoreChaosRepository`` — this file's own
responsibility is narrowed to "where do the JSON bytes come from" (the local
filesystem), which is the only thing genuinely specific to this backend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.adapters.chaos.chaos_tier_index import ChaosFileMeta, ChaosTierIndex, parse_tier_id
from src.adapters.chaos.species_normalize import normalize_species
from src.domain.exceptions import ChaosDataError


@dataclass(frozen=True)
class ChaosFile(ChaosFileMeta):
    """One discovered local Chaos file — its parsed coordinates plus path."""

    path: Path


@runtime_checkable
class ChaosRepositoryLike(Protocol):
    """The narrow subset of ``ChaosRepository`` that ``ChaosAdapter`` and
    ``ChaosStrategyAdapter`` actually depend on — satisfied structurally by
    both ``ChaosRepository`` (local files) and ``FirestoreChaosRepository``
    (see that module), so either can be injected into those adapters without
    either adapter's own code changing at all. A new storage backend (a
    database, a different bucket layout, ...) only ever needs to satisfy
    this shape, per the same Dependency Inversion pattern this project
    already applies to ``CalcEngineAdapter``/``StrategyKnowledgeProvider``.
    """

    def resolve_metagame(self, metagame: str | None) -> str: ...
    def default_metagame(self) -> str: ...
    def ideal_file(self, metagame: str) -> Any: ...
    def current_file(self, metagame: str, rating: int | None) -> Any: ...
    def mon_data(self, file: Any, species: str) -> dict[str, Any] | None: ...
    def resolve_mon(
        self, metagame: str, species: str
    ) -> tuple[dict[str, Any], str] | None: ...


class ChaosRepository:
    """Indexes local Chaos files in a directory and resolves tiers / reg fallback."""

    def __init__(self, path: str | Path, reg_fallback_depth: int = 3) -> None:
        root = Path(path)
        paths: list[Path]
        if root.is_dir():
            paths = sorted(root.glob("*.json"))
        elif root.is_file():
            paths = [root]
        else:
            raise ChaosDataError(f"Chaos path not found: {root}")

        self._files: list[ChaosFile] = []
        for file_path in paths:
            parsed = self._parse_file(file_path)
            if parsed is not None:
                self._files.append(parsed)
        if not self._files:
            raise ChaosDataError(f"No usable Chaos files found at: {root}")
        self._index: ChaosTierIndex[ChaosFile] = ChaosTierIndex(
            self._files, reg_fallback_depth=reg_fallback_depth
        )
        self._cache: dict[Path, dict[str, Any]] = {}

    # -- discovery ------------------------------------------------------- #

    @staticmethod
    def _parse_file(path: Path) -> ChaosFile | None:
        meta = parse_tier_id(path.stem)
        if meta is None:
            return None
        return ChaosFile(
            path=path, metagame=meta.metagame, cutoff=meta.cutoff, gen=meta.gen,
            franchise=meta.franchise, year=meta.year, reg=meta.reg,
        )

    def _load(self, file: ChaosFile) -> dict[str, Any]:
        if file.path not in self._cache:
            try:
                with file.path.open("r", encoding="utf-8") as handle:
                    self._cache[file.path] = json.load(handle)
            except (json.JSONDecodeError, OSError) as exc:
                raise ChaosDataError(f"Unable to read Chaos file {file.path}: {exc}") from exc
        return self._cache[file.path]

    # -- selection (delegates to the shared, storage-agnostic index) ----- #

    def metagames(self) -> set[str]:
        return self._index.metagames()

    def knows(self, metagame: str | None) -> bool:
        return self._index.knows(metagame)

    def resolve_metagame(self, metagame: str | None) -> str:
        return self._index.resolve_metagame(metagame)

    def default_metagame(self) -> str:
        return self._index.default_metagame()

    def ideal_file(self, metagame: str) -> ChaosFile | None:
        return self._index.ideal_file(metagame)

    def current_file(self, metagame: str, rating: int | None) -> ChaosFile | None:
        return self._index.current_file(metagame, rating)

    def reg_fallback_files(self, metagame: str) -> list[ChaosFile]:
        return self._index.reg_fallback_files(metagame)

    # -- data access ----------------------------------------------------- #

    def mon_data(self, file: ChaosFile, species: str) -> dict[str, Any] | None:
        """Return the raw per-Pokemon block, resolving forme/spelling.

        Tries the exact name, then a normalized match, then progressively drops
        trailing forme segments (``Raichu-Mega-Y`` -> ``Raichu-Mega`` ->
        ``Raichu``) so mega/regional/paradox spellings resolve to the base entry.
        """
        data: dict[str, dict[str, Any]] = self._load(file).get("data") or {}
        if species in data:
            return data[species]
        index = {normalize_species(k): k for k in data}
        parts = species.split("-")
        for cut in range(len(parts), 0, -1):
            candidate = "-".join(parts[:cut])
            key = index.get(normalize_species(candidate))
            if key:
                return data[key]
        return None

    def resolve_mon(
        self, metagame: str, species: str
    ) -> tuple[dict[str, Any], str] | None:
        """Find a Pokemon's data in the ideal tier, then walk reg fallback.

        Returns ``(mon_data, source_label)`` or ``None`` if not found anywhere.
        """
        ideal = self.ideal_file(metagame)
        chain = [ideal] if ideal else []
        chain += self.reg_fallback_files(metagame)
        for i, file in enumerate(chain):
            if file is None:
                continue
            data = self.mon_data(file, species)
            if data:
                suffix = " (fallback)" if i > 0 else ""
                return data, f"{file.metagame}@{file.cutoff}{suffix}"
        return None

    def metagame_info(self, file: ChaosFile) -> str:
        info: dict[str, Any] = self._load(file).get("info") or {}
        return str(info.get("metagame", file.metagame))
