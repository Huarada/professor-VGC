"""Tests for the Firestore-backed Chaos repository, against a fake client.

Mirrors test_chaos_repository.py's exact fixture data (same species, same
tiers) so the two backends' behavior can be directly compared — the whole
point of FirestoreChaosRepository is that it's a drop-in replacement for
ChaosRepository, so anywhere the two tests diverge in what they assert would
be a real bug.

google-cloud-firestore is a normal (optional) dependency, not required for
this module's own logic under test here — everything below runs against a
lightweight in-memory fake modeling just the three Firestore SDK calls this
repository actually makes (collection().stream(), collection().document().
collection().document().get(), client.close()), so this file needs neither
the real package nor network/credentials to run.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.adapters.chaos.chaos_adapter import ChaosAdapter
from src.adapters.chaos.firestore_chaos_repository import FirestoreChaosRepository
from src.adapters.smogon.smogon_strategy_adapter import ChaosStrategyAdapter
from src.domain.exceptions import ChaosDataError


def _mon() -> dict[str, Any]:
    return {
        "Raw count": 100, "Abilities": {"Levitate": 100}, "Items": {"Leftovers": 50},
        "Moves": {"Protect": 90, "Earth Power": 80}, "Spreads": {"Modest:0/0/0/32/0/32": 40},
        "Teammates": {"Incineroar": 30}, "Checks and Counters": {"Ting-Lu": {"p": 0.6}},
    }


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class _FakeDocRef:
    def __init__(self, store: dict[tuple[str, ...], dict[str, Any]], path: tuple[str, ...]) -> None:
        self._store = store
        self._path = path

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._path[-1], self._store.get(self._path))

    def collection(self, name: str) -> "_FakeCollectionRef":
        return _FakeCollectionRef(self._store, self._path + (name,))


class _FakeCollectionRef:
    def __init__(self, store: dict[tuple[str, ...], dict[str, Any]], path: tuple[str, ...]) -> None:
        self._store = store
        self._path = path

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, self._path + (doc_id,))

    def stream(self) -> list[_FakeSnapshot]:
        depth = len(self._path) + 1
        return [
            _FakeSnapshot(key[-1], value)
            for key, value in self._store.items()
            if len(key) == depth and key[: len(self._path)] == self._path
        ]


class _FakeFirestoreClient:
    """In-memory stand-in for google.cloud.firestore.Client — a flat dict
    keyed by full path tuples, e.g. ("chaos_tiers", "<tier-id>") for a tier
    doc, ("chaos_tiers", "<tier-id>", "species", "<normalized-id>") for a
    species doc."""

    def __init__(self, store: dict[tuple[str, ...], dict[str, Any]]) -> None:
        self._store = store
        self.closed = False

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._store, (name,))

    def close(self) -> None:
        self.closed = True


def _seed_store() -> dict[tuple[str, ...], dict[str, Any]]:
    """Same tiers/species as test_chaos_repository.py's `chaos_dir` fixture."""
    store: dict[tuple[str, ...], dict[str, Any]] = {}

    def add_tier(metagame: str, cutoff: int, species: dict[str, dict[str, Any]]) -> None:
        tier_id = f"{metagame}-{cutoff}"
        store[("chaos_tiers", tier_id)] = {"info": {"metagame": metagame, "cutoff": cutoff}}
        for name, mon in species.items():
            normalized = name.lower().replace(" ", "").replace("-", "").replace("'", "")
            store[("chaos_tiers", tier_id, "species", normalized)] = {
                **mon, "original_name": name,
            }

    add_tier("gen9championsvgc2026regmb", 1760, {"Chi-Yu": _mon(), "Flutter Mane": _mon()})
    add_tier("gen9championsvgc2026regmb", 1500, {"Chi-Yu": _mon()})
    add_tier("gen9championsvgc2026regmb", 0, {"Chi-Yu": _mon()})
    add_tier("gen9championsvgc2026regma", 1760, {"Ogerpon": _mon()})  # previous regulation
    add_tier("gen9vgc2026regmb", 1760, {"Miraidon": _mon()})  # different game
    return store


@pytest.fixture
def fake_repo() -> FirestoreChaosRepository:
    client = _FakeFirestoreClient(_seed_store())
    return FirestoreChaosRepository(project_id="test-project", client=client)


def test_ideal_and_current_tier(fake_repo: FirestoreChaosRepository) -> None:
    meta = "gen9championsvgc2026regmb"
    assert fake_repo.ideal_file(meta).cutoff == 1760  # type: ignore[union-attr]
    assert fake_repo.current_file(meta, 1206).cutoff == 0  # type: ignore[union-attr]
    assert fake_repo.current_file(meta, 1550).cutoff == 1500  # type: ignore[union-attr]
    assert fake_repo.current_file(meta, 9999).cutoff == 1760  # type: ignore[union-attr]


def test_default_metagame_is_newest(fake_repo: FirestoreChaosRepository) -> None:
    assert fake_repo.default_metagame() == "gen9championsvgc2026regmb"


def test_reg_fallback_stays_in_same_game(fake_repo: FirestoreChaosRepository) -> None:
    fallbacks = fake_repo.reg_fallback_files("gen9championsvgc2026regmb")
    metas = {f.metagame for f in fallbacks}
    assert "gen9championsvgc2026regma" in metas
    assert "gen9vgc2026regmb" not in metas


def test_mon_data_resolves_forme_via_normalized_id(fake_repo: FirestoreChaosRepository) -> None:
    """A species like "Raichu-Mega-Y" must resolve by progressively
    stripping forme segments, one direct doc read per candidate — same
    behavior as ChaosRepository.mon_data, over Firestore documents."""
    client = _FakeFirestoreClient(_seed_store())
    # Store "Raichu" under its own tier for this one test.
    client._store[("chaos_tiers", "gen9vgc2025regh-0", "species", "raichu")] = {
        **_mon(), "original_name": "Raichu",
    }
    client._store[("chaos_tiers", "gen9vgc2025regh-0")] = {"info": {"metagame": "gen9vgc2025regh"}}
    repo = FirestoreChaosRepository(project_id="test-project", client=client)
    file = repo.ideal_file("gen9vgc2025regh")
    assert file is not None
    data = repo.mon_data(file, "Raichu-Mega-Y")
    assert data is not None
    assert data["original_name"] == "Raichu"


def test_species_not_found_returns_none(fake_repo: FirestoreChaosRepository) -> None:
    file = fake_repo.ideal_file("gen9championsvgc2026regmb")
    assert file is not None
    assert fake_repo.mon_data(file, "Nonexistentmon") is None


def test_species_lookup_is_cached_after_first_read(fake_repo: FirestoreChaosRepository) -> None:
    """A repeat lookup for the same species must not re-touch the fake
    client's store lookup path a second time — verified indirectly by
    mutating the backing store after the first read and confirming the
    second read still returns the ORIGINAL (cached) value."""
    file = fake_repo.ideal_file("gen9championsvgc2026regmb")
    assert file is not None
    first = fake_repo.mon_data(file, "Chi-Yu")
    assert first is not None
    # Corrupt the backing store directly — a real re-read would now fail/differ.
    fake_repo._client._store[("chaos_tiers", file.doc_id, "species", "chiyu")] = None  # type: ignore[attr-defined]
    second = fake_repo.mon_data(file, "Chi-Yu")
    assert second == first  # served from cache, not re-fetched


def test_resolve_mon_walks_reg_fallback(fake_repo: FirestoreChaosRepository) -> None:
    resolved = fake_repo.resolve_mon("gen9championsvgc2026regmb", "Ogerpon")
    assert resolved is not None
    _data, source = resolved
    assert "(fallback)" in source
    assert "regma" in source


def test_metagame_info_reads_the_stored_tier_metadata(fake_repo: FirestoreChaosRepository) -> None:
    file = fake_repo.ideal_file("gen9championsvgc2026regmb")
    assert file is not None
    assert fake_repo.metagame_info(file) == "gen9championsvgc2026regmb"


def test_close_delegates_to_the_underlying_client(fake_repo: FirestoreChaosRepository) -> None:
    fake_repo.close()
    assert fake_repo._client.closed  # type: ignore[attr-defined]


def test_no_tiers_raises_chaos_data_error() -> None:
    with pytest.raises(ChaosDataError):
        FirestoreChaosRepository(project_id="test-project", client=_FakeFirestoreClient({}))


# --- integration with ChaosAdapter/ChaosStrategyAdapter via `repository=` --- #


def test_chaos_adapter_over_firestore_repository(fake_repo: FirestoreChaosRepository) -> None:
    ctx = ChaosAdapter(repository=fake_repo).build_match_context(
        ["Chi-Yu"], metagame="gen9championsvgc2026regmb", rating=1206
    )
    assert "@1760" in ctx.pokemon_stats["Chi-Yu"].source
    assert "current tier" in ctx.rating_note
    assert ctx.current_tier_stats


def test_strategy_adapter_over_firestore_repository(fake_repo: FirestoreChaosRepository) -> None:
    strat = ChaosStrategyAdapter(repository=fake_repo).get_strategy(
        "Ogerpon", metagame="gen9championsvgc2026regmb"
    )
    assert "regma" in strat.overview and "fallback" in strat.overview


def test_chaos_adapter_requires_path_or_repository() -> None:
    with pytest.raises(Exception):  # noqa: B017 - ConfigurationError, a ProfessorVGCError subclass
        ChaosAdapter()
