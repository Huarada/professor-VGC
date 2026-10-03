"""Firestore-backed Chaos repository (same contract as ``ChaosRepository``).

    chaos_tiers/{tier_id}                       # e.g. gen9championsvgc2026regmb-1760
        info, species_index
        species/{normalized_species_id}         # the species' Chaos JSON, verbatim

``tier_id`` is the local file name without ``.json``, so tier selection is
the shared ``ChaosTierIndex``. One document per species (a tier file is
2.5-4.5MB, past Firestore's 1MiB limit) makes each lookup one direct read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.adapters.chaos.chaos_tier_index import (
    ChaosFileMeta,
    ChaosTierIndex,
    info_mismatch,
    parse_tier_id,
)
from src.adapters.chaos.species_normalize import normalize_species
# The shared factory imports google-cloud-firestore lazily.
from src.adapters.firestore_client import build_firestore_client as _build_client
from src.domain.exceptions import ChaosDataError

_SPECIES_SUBCOLLECTION = "species"


@dataclass(frozen=True)
class FirestoreChaosFile(ChaosFileMeta):
    """One tier's coordinates plus its Firestore document id."""

    doc_id: str


class FirestoreChaosRepository:
    """``ChaosRepositoryLike`` sourced from Firestore."""

    def __init__(
        self,
        project_id: str = "",
        *,
        database_id: str = "(default)",
        collection: str = "chaos_tiers",
        credentials_path: str | None = None,
        grpc_ca_bundle_path: str | None = None,
        reg_fallback_depth: int = 3,
        client: Any | None = None,
    ) -> None:
        # `client` is a test seam; production always builds one.
        self._client = client if client is not None else _build_client(
            project_id, database_id, credentials_path, grpc_ca_bundle_path
        )
        self._collection_name = collection

        self._files: list[FirestoreChaosFile] = []
        self._tier_info: dict[str, dict[str, Any]] = {}
        self._rejected: dict[str, str] = {}
        self._legal_cache: dict[str, frozenset[str]] = {}
        try:
            tier_docs = list(self._client.collection(collection).stream())
        except Exception as exc:  # noqa: BLE001 - many concrete gRPC/auth exception types
            raise ChaosDataError(
                f"Unable to list Chaos tiers from Firestore collection "
                f"'{collection}' (project={project_id}): {exc}"
            ) from exc
        for doc in tier_docs:
            meta = parse_tier_id(doc.id)
            if meta is None:
                continue
            fields = doc.to_dict() or {}
            reason = info_mismatch(meta.metagame, fields.get("info"))
            if reason is not None:
                # Never serve another format's data under this tier's name
                # (e.g. singles BSS stats stored as a VGC tier).
                self._rejected[doc.id] = reason
                continue
            self._files.append(
                FirestoreChaosFile(
                    doc_id=doc.id, metagame=meta.metagame, cutoff=meta.cutoff,
                    gen=meta.gen, franchise=meta.franchise, year=meta.year, reg=meta.reg,
                )
            )
            self._tier_info[doc.id] = fields
        if not self._files:
            rejected = "; ".join(f"{k}: {v}" for k, v in self._rejected.items())
            raise ChaosDataError(
                f"No usable Chaos tiers found in Firestore collection '{collection}' "
                f"(project={project_id}) — run scripts/sync_smogon_chaos_to_firestore.py."
                + (f" Rejected tiers: {rejected}" if rejected else "")
            )
        self._index: ChaosTierIndex[FirestoreChaosFile] = ChaosTierIndex(
            self._files, reg_fallback_depth=reg_fallback_depth
        )
        # (tier doc id, normalized species) -> fields, or None for a miss; one
        # Firestore read per new key.
        self._species_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    # -- selection (delegates to the shared, storage-agnostic index) ----- #

    def metagames(self) -> set[str]:
        return self._index.metagames()

    def knows(self, metagame: str | None) -> bool:
        return self._index.knows(metagame)

    def resolve_metagame(self, metagame: str | None) -> str:
        return self._index.resolve_metagame(metagame)

    def default_metagame(self) -> str:
        return self._index.default_metagame()

    def ideal_file(self, metagame: str) -> FirestoreChaosFile | None:
        return self._index.ideal_file(metagame)

    def current_file(self, metagame: str, rating: int | None) -> FirestoreChaosFile | None:
        return self._index.current_file(metagame, rating)

    def reg_fallback_files(self, metagame: str) -> list[FirestoreChaosFile]:
        return self._index.reg_fallback_files(metagame)

    # -- data access ------------------------------------------------------ #

    def _fetch_species_doc(self, tier_doc_id: str, normalized_id: str) -> dict[str, Any] | None:
        cache_key = (tier_doc_id, normalized_id)
        if cache_key in self._species_cache:
            return self._species_cache[cache_key]
        try:
            snapshot = (
                self._client.collection(self._collection_name)
                .document(tier_doc_id)
                .collection(_SPECIES_SUBCOLLECTION)
                .document(normalized_id)
                .get()
            )
        except Exception as exc:  # noqa: BLE001 - many concrete gRPC/auth exception types
            raise ChaosDataError(
                f"Unable to read species '{normalized_id}' from Firestore tier "
                f"'{tier_doc_id}': {exc}"
            ) from exc
        data = snapshot.to_dict() if snapshot.exists else None
        self._species_cache[cache_key] = data
        return data

    def mon_data(self, file: FirestoreChaosFile, species: str) -> dict[str, Any] | None:
        """Same forme/spelling resolution as ``ChaosRepository.mon_data``; each
        candidate is one direct document read.
        """
        parts = species.split("-")
        for cut in range(len(parts), 0, -1):
            candidate = "-".join(parts[:cut])
            data = self._fetch_species_doc(file.doc_id, normalize_species(candidate))
            if data:
                return data
        return None

    def resolve_mon(
        self, metagame: str, species: str
    ) -> tuple[dict[str, Any], str] | None:
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
        """Tier documents refused because their info.metagame names another format."""
        return dict(self._rejected)

    def legal_species(self, metagame: str) -> frozenset[str]:
        """Normalized species with data in any tier of ``metagame``: from the tier's
        species_index when present, else by listing its species once (cached).
        """
        if metagame in self._legal_cache:
            return self._legal_cache[metagame]
        names: set[str] = set()
        for file in self._files:
            if file.metagame != metagame:
                continue
            index = self._tier_info.get(file.doc_id, {}).get("species_index")
            if isinstance(index, list):
                names.update(normalize_species(str(n)) for n in index)
                continue
            try:
                refs = (
                    self._client.collection(self._collection_name)
                    .document(file.doc_id)
                    .collection(_SPECIES_SUBCOLLECTION)
                    .list_documents()
                )
                names.update(ref.id for ref in refs)
            except Exception as exc:  # noqa: BLE001 - many concrete gRPC/auth exception types
                raise ChaosDataError(
                    f"Unable to list species of Firestore tier '{file.doc_id}': {exc}"
                ) from exc
        self._legal_cache[metagame] = frozenset(names)
        return self._legal_cache[metagame]

    def metagame_info(self, file: FirestoreChaosFile) -> str:
        info = self._tier_info.get(file.doc_id, {}).get("info") or {}
        return str(info.get("metagame", file.metagame))

    def close(self) -> None:
        """Release the underlying gRPC channel."""
        self._client.close()
