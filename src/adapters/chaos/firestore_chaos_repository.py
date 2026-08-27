"""Firestore-backed Chaos repository — same contract as ``ChaosRepository``,
sourced from Google Cloud Firestore instead of local ``data/chaos/*.json``
files.

Storage layout (populated by ``scripts/migrate_chaos_to_firestore.py``):

    chaos_tiers/{tier_id}                          # e.g. "gen9championsvgc2026regmb-1760"
        info: <the file's original "info" object>  # metagame, cutoff, battle count, ...
        chaos_tiers/{tier_id}/species/{normalized_species_id}
            <the species' original Chaos JSON object, verbatim>  # Abilities/Items/
            original_name: <species>                             # Moves/Spreads/Teammates/
                                                                    # Checks and Counters/Raw count

``tier_id`` is exactly a local filename with ``.json`` stripped (e.g.
``gen9championsvgc2026regmb-1760``) — the SAME string shape
``chaos_tier_index.parse_tier_id`` already parses, so tier/regulation
selection is 100% shared code with the local-file backend
(``ChaosTierIndex``), not a second implementation.

Cost shape (why this layout, not "one document per file"): a whole Chaos
tier file is 2.5-4.5MB with 250-300+ species — one Firestore document per
FILE would routinely blow past the 1MiB document size limit, and would mean
every ``build_match_context`` call re-downloads every species in a tier even
though a real battle only ever needs the handful actually in play. Storing
one document PER SPECIES, keyed by its own normalized name, means a lookup
is a single direct document read by id (no listing/scanning a collection to
find it) — the cheapest possible shape under Firestore's per-document-read
billing model. The species doc's field content is the ORIGINAL Chaos JSON
object unchanged, satisfying "unstructured JSON as today" while still living
in Firestore's native document/map representation (no re-serialization to a
string blob needed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.adapters.chaos.chaos_tier_index import ChaosFileMeta, ChaosTierIndex, parse_tier_id
from src.adapters.chaos.species_normalize import normalize_species
from src.domain.exceptions import ChaosDataError, ConfigurationError

if TYPE_CHECKING:
    # Type-checking only — kept lazy at runtime (see __init__ below) so this
    # module remains importable without google-cloud-firestore installed,
    # matching this project's langchain_provider.py/adk_provider.py pattern:
    # an optional BYOK-style dependency degrades to ConfigurationError, not
    # an ImportError at module load time.
    from google.cloud.firestore import Client

_SPECIES_SUBCOLLECTION = "species"


@dataclass(frozen=True)
class FirestoreChaosFile(ChaosFileMeta):
    """One tier's coordinates plus its Firestore document id."""

    doc_id: str


class FirestoreChaosRepository:
    """``ChaosRepositoryLike`` sourced from Firestore (see
    ``chaos_repository.ChaosRepositoryLike`` for the exact contract this
    satisfies structurally — no shared base class needed)."""

    def __init__(
        self,
        project_id: str = "",
        *,
        database_id: str = "(default)",
        collection: str = "chaos_tiers",
        credentials_path: str | None = None,
        reg_fallback_depth: int = 3,
        client: Any | None = None,
    ) -> None:
        # `client` is a test seam (an already-built/fake Firestore client),
        # the same shape as AdkAnalysisOrchestrator's `model` param or
        # LangChainAnalysisOrchestrator's `chat_model` param — production
        # code never passes it, always going through `_build_client` below.
        self._client = client if client is not None else _build_client(
            project_id, database_id, credentials_path
        )
        self._collection_name = collection

        self._files: list[FirestoreChaosFile] = []
        self._tier_info: dict[str, dict[str, Any]] = {}
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
            self._files.append(
                FirestoreChaosFile(
                    doc_id=doc.id, metagame=meta.metagame, cutoff=meta.cutoff,
                    gen=meta.gen, franchise=meta.franchise, year=meta.year, reg=meta.reg,
                )
            )
            self._tier_info[doc.id] = doc.to_dict() or {}
        if not self._files:
            raise ChaosDataError(
                f"No usable Chaos tiers found in Firestore collection '{collection}' "
                f"(project={project_id}) — run scripts/migrate_chaos_to_firestore.py first."
            )
        self._index: ChaosTierIndex[FirestoreChaosFile] = ChaosTierIndex(
            self._files, reg_fallback_depth=reg_fallback_depth
        )
        # (tier_doc_id, normalized species candidate) -> species doc fields,
        # or None for a confirmed miss. One real Firestore read per NEW key;
        # every repeat lookup within this process's lifetime is free.
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
        """Same forme/spelling resolution as ``ChaosRepository.mon_data``,
        but each candidate is a single direct document read by its
        normalized id — never a full-collection scan."""
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

    def metagame_info(self, file: FirestoreChaosFile) -> str:
        info = self._tier_info.get(file.doc_id, {}).get("info") or {}
        return str(info.get("metagame", file.metagame))

    def close(self) -> None:
        """Release the underlying gRPC channel."""
        self._client.close()


def _build_client(
    project_id: str, database_id: str, credentials_path: str | None
) -> "Client":
    try:
        from google.cloud import firestore
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ConfigurationError(
            "The 'google-cloud-firestore' package is not installed. "
            "Run: pip install google-cloud-firestore"
        ) from exc
    if not project_id:
        raise ConfigurationError(
            "A GCP project id is required (PROFESSORVGC_FIRESTORE_PROJECT_ID)"
        )
    if credentials_path:
        try:
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'google-auth' package is not installed. Run: pip install google-auth"
            ) from exc
        try:
            # google-auth's own stubs don't type this classmethod's return
            # (a stub-completeness gap, not a real typing issue — mirrors
            # the same class of gap noted in gemini_provider.py).
            credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_path
            )
        except (OSError, ValueError) as exc:
            raise ConfigurationError(
                f"Unable to load the Firestore service account key at "
                f"'{credentials_path}': {exc}"
            ) from exc
        return firestore.Client(
            project=project_id, database=database_id, credentials=credentials
        )
    # No explicit key path: fall back to Application Default Credentials
    # (gcloud auth application-default login, or GOOGLE_APPLICATION_CREDENTIALS).
    return firestore.Client(project=project_id, database=database_id)
