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
adapters that consume this repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.exceptions import ChaosDataError

# gen9 [champions] vgc 2026 reg mb  ->  franchise groups Champions vs base VGC.
_META_RE = re.compile(
    r"^(?P<gen>gen\d+)(?P<franchise>[a-z]*?)vgc(?P<year>\d{4})reg(?P<reg>[a-z]+)$"
)
_FILE_RE = re.compile(r"^(?P<metagame>.+?)(?:-(?P<cutoff>\d+))?\.json$")


@dataclass(frozen=True)
class ChaosFile:
    """One discovered Chaos file and its parsed coordinates."""

    path: Path
    metagame: str
    cutoff: int
    gen: str
    franchise: str  # "champions" or "" (base VGC) — the game family
    year: int
    reg: str

    @property
    def game_key(self) -> str:
        """Franchise-level key (year-independent) used to group same-game regs."""
        return f"{self.gen}{self.franchise}"

    @property
    def reg_order(self) -> tuple[int, str]:
        """Sortable key: newer year first, then later regulation letter."""
        return (self.year, self.reg)

    @property
    def label(self) -> str:
        return f"{self.metagame}@{self.cutoff}"


class ChaosRepository:
    """Indexes Chaos files in a directory and resolves tiers / reg fallback."""

    def __init__(self, path: str | Path, reg_fallback_depth: int = 3) -> None:
        self._depth = max(0, int(reg_fallback_depth))
        root = Path(path)
        files: list[Path]
        if root.is_dir():
            files = sorted(root.glob("*.json"))
        elif root.is_file():
            files = [root]
        else:
            raise ChaosDataError(f"Chaos path not found: {root}")

        self._files: list[ChaosFile] = []
        for file in files:
            parsed = self._parse_file(file)
            if parsed is not None:
                self._files.append(parsed)
        if not self._files:
            raise ChaosDataError(f"No usable Chaos files found at: {root}")
        self._cache: dict[Path, dict[str, Any]] = {}

    # -- discovery ------------------------------------------------------- #

    @staticmethod
    def _parse_file(path: Path) -> ChaosFile | None:
        m = _FILE_RE.match(path.name)
        if not m:
            return None
        metagame = m.group("metagame")
        cutoff = int(m.group("cutoff") or 0)
        meta = _META_RE.match(metagame)
        if meta is None:
            # Unrecognized naming: still usable as a single, tier-0, standalone meta.
            return ChaosFile(path, metagame, cutoff, "gen0", metagame, 0, "")
        return ChaosFile(
            path=path,
            metagame=metagame,
            cutoff=cutoff,
            gen=meta.group("gen"),
            franchise=meta.group("franchise"),
            year=int(meta.group("year")),
            reg=meta.group("reg"),
        )

    def _load(self, file: ChaosFile) -> dict[str, Any]:
        if file.path not in self._cache:
            try:
                with file.path.open("r", encoding="utf-8") as handle:
                    self._cache[file.path] = json.load(handle)
            except (json.JSONDecodeError, OSError) as exc:
                raise ChaosDataError(f"Unable to read Chaos file {file.path}: {exc}") from exc
        return self._cache[file.path]

    # -- selection ------------------------------------------------------- #

    def metagames(self) -> set[str]:
        return {f.metagame for f in self._files}

    def knows(self, metagame: str | None) -> bool:
        return bool(metagame) and metagame in self.metagames()

    def resolve_metagame(self, metagame: str | None) -> str:
        """Return the given metagame if known, else the newest available one."""
        if self.knows(metagame):
            assert metagame is not None  # knows() is False for None, so this always holds
            return metagame
        return self.default_metagame()

    def default_metagame(self) -> str:
        """Newest metagame available (latest year, latest regulation)."""
        newest = max(self._files, key=lambda f: f.reg_order)
        return newest.metagame

    def _files_for(self, metagame: str) -> list[ChaosFile]:
        return sorted(
            (f for f in self._files if f.metagame == metagame), key=lambda f: f.cutoff
        )

    def ideal_file(self, metagame: str) -> ChaosFile | None:
        files = self._files_for(metagame)
        return files[-1] if files else None

    def current_file(self, metagame: str, rating: int | None) -> ChaosFile | None:
        """The tier bracket a rating falls into (largest cutoff <= rating)."""
        files = self._files_for(metagame)
        if not files:
            return None
        if rating is None:
            return files[-1]
        eligible = [f for f in files if f.cutoff <= rating]
        return (eligible or files)[0] if not eligible else eligible[-1]

    def reg_fallback_files(self, metagame: str) -> list[ChaosFile]:
        """Ideal-tier files of older regulations in the SAME game family."""
        current = next((f for f in self._files if f.metagame == metagame), None)
        if current is None:
            return []
        older_metas: dict[str, ChaosFile] = {}
        for f in self._files:
            if f.game_key != current.game_key:
                continue  # stay within the same game (Champions -> Champions)
            if f.reg_order >= current.reg_order:
                continue  # only strictly older regulations
            # keep the highest-cutoff (ideal) file per older metagame
            best = older_metas.get(f.metagame)
            if best is None or f.cutoff > best.cutoff:
                older_metas[f.metagame] = f
        ordered = sorted(older_metas.values(), key=lambda f: f.reg_order, reverse=True)
        return ordered[: self._depth]

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
        index = {_normalize(k): k for k in data}
        parts = species.split("-")
        for cut in range(len(parts), 0, -1):
            candidate = "-".join(parts[:cut])
            key = index.get(_normalize(candidate))
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


def _normalize(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace(".", "").replace("'", "")
