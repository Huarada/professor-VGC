"""Storage-agnostic Chaos tier index — rating-cutoff selection and
regulation-fallback resolution, shared by every ``ChaosRepository`` backend
(local files, Firestore, or any future source).

Extracted from what was originally file-discovery logic embedded directly in
``ChaosRepository`` so a non-file-backed repository (see
``firestore_chaos_repository.py``) does not have to duplicate the tier
(ideal/current-bracket) selection and regulation-fallback rules — both
backends parse the SAME ``<metagame>-<ratingCutoff>`` identifier shape
(a local filename with ``.json`` stripped, or a Firestore tier document id)
into the same coordinates and hand them to this one class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

from src.domain.exceptions import ChaosDataError

# gen9 [champions] vgc 2026 reg mb  ->  franchise groups Champions vs base VGC.
_META_RE = re.compile(
    r"^(?P<gen>gen\d+)(?P<franchise>[a-z]*?)vgc(?P<year>\d{4})reg(?P<reg>[a-z]+)$"
)
_ID_RE = re.compile(r"^(?P<metagame>.+?)(?:-(?P<cutoff>\d+))?$")


@dataclass(frozen=True)
class ChaosFileMeta:
    """One tier's parsed coordinates — everything selection logic needs,
    independent of where the underlying data actually lives."""

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


def parse_tier_id(tier_id: str) -> ChaosFileMeta | None:
    """Parse a ``<metagame>`` or ``<metagame>-<cutoff>`` identifier into its
    coordinates. Returns a tier-0, standalone-metagame ``ChaosFileMeta`` for
    a metagame name that doesn't match the ``genN[franchise]vgcYYYYregX``
    shape (still usable, just never a regulation-fallback candidate for
    anything else), and ``None`` only when the id has no metagame segment
    at all (should not happen for a real tier id)."""
    m = _ID_RE.match(tier_id)
    if not m:
        return None
    metagame = m.group("metagame")
    cutoff = int(m.group("cutoff") or 0)
    meta = _META_RE.match(metagame)
    if meta is None:
        return ChaosFileMeta(metagame, cutoff, "gen0", metagame, 0, "")
    return ChaosFileMeta(
        metagame=metagame,
        cutoff=cutoff,
        gen=meta.group("gen"),
        franchise=meta.group("franchise"),
        year=int(meta.group("year")),
        reg=meta.group("reg"),
    )


_T = TypeVar("_T", bound=ChaosFileMeta)


class ChaosTierIndex(Generic[_T]):
    """Pure in-memory tier selection over an already-discovered list of
    tier descriptors: ideal tier (highest cutoff), current-rating bracket,
    and regulation fallback (same game family, nearest-first, depth-limited).

    Generic over the concrete tier-descriptor type (``_T``, bound to
    ``ChaosFileMeta``) so each backend can attach its own storage handle
    (a local ``Path`` for ``ChaosRepository``, a Firestore document id for
    ``FirestoreChaosRepository``) while sharing this exact selection logic.
    """

    def __init__(self, files: Sequence[_T], reg_fallback_depth: int = 3) -> None:
        self._depth = max(0, int(reg_fallback_depth))
        self._files: list[_T] = list(files)
        if not self._files:
            raise ChaosDataError("No usable Chaos tiers found")

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

    def _files_for(self, metagame: str) -> list[_T]:
        return sorted(
            (f for f in self._files if f.metagame == metagame), key=lambda f: f.cutoff
        )

    def ideal_file(self, metagame: str) -> _T | None:
        files = self._files_for(metagame)
        return files[-1] if files else None

    def current_file(self, metagame: str, rating: int | None) -> _T | None:
        """The tier bracket a rating falls into (largest cutoff <= rating)."""
        files = self._files_for(metagame)
        if not files:
            return None
        if rating is None:
            return files[-1]
        eligible = [f for f in files if f.cutoff <= rating]
        return (eligible or files)[0] if not eligible else eligible[-1]

    def reg_fallback_files(self, metagame: str) -> list[_T]:
        """Ideal-tier files of older regulations in the SAME game family."""
        current = next((f for f in self._files if f.metagame == metagame), None)
        if current is None:
            return []
        older_metas: dict[str, _T] = {}
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
