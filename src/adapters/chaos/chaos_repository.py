"""Local-file Chaos repository (``<metagame>-<cutoff>.json`` files).

Tier selection (ideal tier, current rating bracket, same-game regulation
fallback) is the shared ``ChaosTierIndex``; this module only finds and
reads the files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.adapters.chaos.chaos_tier_index import (
    ChaosFileMeta,
    ChaosTierIndex,
    info_mismatch,
    parse_tier_id,
)
from src.adapters.chaos.species_normalize import normalize_species
from src.domain.exceptions import ChaosDataError


@dataclass(frozen=True)
class ChaosFile(ChaosFileMeta):
    """One discovered local Chaos file — its parsed coordinates plus path."""

    path: Path


@runtime_checkable
class ChaosRepositoryLike(Protocol):
    """What ``ChaosAdapter`` / ``ChaosStrategyAdapter`` need; satisfied
    structurally by the local and Firestore repositories.
    """

    def metagames(self) -> set[str]: ...
    def resolve_metagame(self, metagame: str | None) -> str: ...
    def default_metagame(self) -> str: ...
    def ideal_file(self, metagame: str) -> Any: ...
    def current_file(self, metagame: str, rating: int | None) -> Any: ...
    def reg_fallback_files(self, metagame: str) -> list[Any]: ...
    def mon_data(self, file: Any, species: str) -> dict[str, Any] | None: ...
    def resolve_mon(
        self, metagame: str, species: str
    ) -> tuple[dict[str, Any], str] | None: ...
    def legal_species(self, metagame: str) -> frozenset[str]: ...
    @property
    def rejected_tiers(self) -> dict[str, str]: ...


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
        self._cache: dict[Path, dict[str, Any]] = {}
        self._rejected: dict[str, str] = {}
        for file_path in paths:
            parsed = self._parse_file(file_path)
            if parsed is None:
                continue
            reason = info_mismatch(parsed.metagame, self._load(parsed).get("info"))
            if reason is not None:
                # Never serve another format's data under this file's name
                # (e.g. singles BSS stats saved as a VGC file).
                self._rejected[file_path.name] = reason
                self._cache.pop(file_path, None)
                continue
            self._files.append(parsed)
        if not self._files:
            rejected = "; ".join(f"{k}: {v}" for k, v in self._rejected.items())
            raise ChaosDataError(
                f"No usable Chaos files found at: {root}"
                + (f" (rejected: {rejected})" if rejected else "")
            )
        self._index: ChaosTierIndex[ChaosFile] = ChaosTierIndex(
            self._files, reg_fallback_depth=reg_fallback_depth
        )

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
        """A Pokemon's raw block: exact name, then normalized, then dropping trailing
        forme segments (``Raichu-Mega-Y`` -> ``Raichu-Mega`` -> ``Raichu``).
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

    @property
    def rejected_tiers(self) -> dict[str, str]:
        """Files refused because their own info.metagame names another format."""
        return dict(self._rejected)

    def legal_species(self, metagame: str) -> frozenset[str]:
        """Normalized names of every species with data in ANY tier of
        ``metagame`` (never another format's)."""
        names: set[str] = set()
        for file in self._files:
            if file.metagame == metagame:
                names.update(normalize_species(k) for k in (self._load(file).get("data") or {}))
        return frozenset(names)


class StrictRegulationRepository:
    """A repository view with no regulation fallback, for pinned analyses."""

    def __init__(self, inner: ChaosRepositoryLike) -> None:
        self._inner = inner

    def metagames(self) -> set[str]:
        return self._inner.metagames()

    def resolve_metagame(self, metagame: str | None) -> str:
        return self._inner.resolve_metagame(metagame)

    def default_metagame(self) -> str:
        return self._inner.default_metagame()

    def ideal_file(self, metagame: str) -> Any:
        return self._inner.ideal_file(metagame)

    def current_file(self, metagame: str, rating: int | None) -> Any:
        return self._inner.current_file(metagame, rating)

    def reg_fallback_files(self, metagame: str) -> list[Any]:
        return []

    def mon_data(self, file: Any, species: str) -> dict[str, Any] | None:
        return self._inner.mon_data(file, species)

    def resolve_mon(self, metagame: str, species: str) -> tuple[dict[str, Any], str] | None:
        ideal = self._inner.ideal_file(metagame)
        if ideal is None:
            return None
        data = self._inner.mon_data(ideal, species)
        return (data, f"{ideal.metagame}@{ideal.cutoff}") if data else None

    def legal_species(self, metagame: str) -> frozenset[str]:
        return self._inner.legal_species(metagame)

    @property
    def rejected_tiers(self) -> dict[str, str]:
        return self._inner.rejected_tiers
